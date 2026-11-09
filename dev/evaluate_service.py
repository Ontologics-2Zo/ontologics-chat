#!/usr/bin/env python3
"""Measure the isolated, deterministic query API without model or business writes.

Private case JSON: {"schema_version":1,"cases":[{"id":"case-1",
"request":{"question":"...","ledger_version":1},"expected_http_status":200,
"assertions":[{"path":"/status","op":"eq","value":"UNKNOWN"}]}]}.
Paths are JSON Pointers; operations are eq, ne, contains, exists, missing and
length_eq. Expected values come only from the case file, never service code.
Optional conversation_id/turn are grouping metadata, NOT server conversation
memory. The current API accepts question, ledger_version, context_entity_id only.

Authentication is a private JSON file with base_url, session_token, csrf_token,
identity_revision, or the four ONTO_EVAL_* environment variables (BASE_URL,
SESSION_TOKEN, CSRF_TOKEN, IDENTITY_REVISION). No login/logout is performed.
Only POST /api/mks2/query is allowed. --isolated is an operator attestation,
not proof of database isolation; the existing main port 8870 is refused.

Latency is monotonic client dispatch through complete response-body receipt,
not server-only duration or model TTFT. Warmups are saved but never aggregated.
Raw requests/responses and results remain in a new private directory outside Git.
Stdout contains counts and fixed codes only, never questions, results or paths.
"""
from __future__ import annotations

import argparse
from collections import Counter
from decimal import Decimal
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
from time import monotonic_ns
from urllib.parse import urlsplit

import httpx


REPO_ROOT = Path(__file__).resolve().parents[1]
ENDPOINT = "/api/mks2/query"
MAX_INPUT_BYTES = 16 * 1024 * 1024
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_SAMPLES = 50000
MISSING = object()
OPS = {"eq", "ne", "contains", "exists", "missing", "length_eq"}
SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}\Z")
CONTRACT_METADATA = {"source", "frozen_at", "profile", "repetitions", "warmup_per_case",
                     "latency_percentile", "concurrency", "seed", "qualitative_case_ids", "metadata"}


class EvaluationError(Exception):
    """Only fixed, public-safe codes cross the CLI boundary."""


def fail(code):
    raise EvaluationError(code)


def valid_base_url(value):
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path or not parsed.port or not 1024 <= parsed.port <= 65535
                or parsed.port == 8870 or value != f"http://{parsed.hostname}:{parsed.port}"):
            fail("ISOLATED_LOOPBACK_ORIGIN_REQUIRED")
    except (ValueError, TypeError):
        fail("ISOLATED_LOOPBACK_ORIGIN_REQUIRED")
    return value


def private_input(path):
    try:
        path = Path(path).expanduser()
        if path.is_symlink():
            fail("PRIVATE_INPUT_REQUIRED")
        path = path.resolve(strict=True)
        mode = path.stat()
        if (path.is_relative_to(REPO_ROOT) or not stat.S_ISREG(mode.st_mode)
                or stat.S_IMODE(mode.st_mode) & 0o077 or mode.st_size > MAX_INPUT_BYTES):
            fail("PRIVATE_INPUT_REQUIRED")
        raw = path.read_bytes()
        if len(raw) > MAX_INPUT_BYTES:
            fail("PRIVATE_INPUT_REQUIRED")
        return raw
    except (OSError, ValueError, TypeError):
        fail("PRIVATE_INPUT_REQUIRED")


def decode_json(raw):
    def constant(_):
        fail("INVALID_JSON")

    def pairs(entries):
        result = {}
        for key, value in entries:
            if key in result:
                fail("INVALID_JSON")
            result[key] = value
        return result
    try:
        return json.loads(raw, parse_float=Decimal, parse_constant=constant, object_pairs_hook=pairs)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        fail("INVALID_JSON")


def pointer_tokens(path):
    if not isinstance(path, str) or len(path) > 2048 or (path and not path.startswith("/")):
        fail("INVALID_ASSERTION_PATH")
    tokens = [] if not path else path[1:].split("/")
    if len(tokens) > 64 or any(re.search(r"~(?![01])", token) for token in tokens):
        fail("INVALID_ASSERTION_PATH")
    return [token.replace("~1", "/").replace("~0", "~") for token in tokens]


def at_pointer(document, path):
    value = document
    for token in pointer_tokens(path):
        if isinstance(value, dict) and token in value:
            value = value[token]
        elif isinstance(value, list) and re.fullmatch(r"0|[1-9][0-9]*", token) and len(token) < 10 and int(token) < len(value):
            value = value[int(token)]
        else:
            return MISSING
    return value


def same_json(left, right):
    if type(left) in (int, Decimal) and type(right) in (int, Decimal):
        return left == right
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(same_json(left[key], right[key]) for key in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(same_json(a, b) for a, b in zip(left, right))
    return left == right


def validate_assertion(rule):
    if not isinstance(rule, dict) or set(rule) - {"path", "op", "value"} or rule.get("op") not in OPS:
        fail("INVALID_ASSERTION")
    pointer_tokens(rule.get("path"))
    if (rule["op"] in {"exists", "missing"}) == ("value" in rule):
        fail("INVALID_ASSERTION")
    if rule["op"] == "length_eq" and (type(rule["value"]) is not int or rule["value"] < 0):
        fail("INVALID_ASSERTION")


def check_assertion(document, rule):
    value, op = at_pointer(document, rule["path"]), rule["op"]
    if op == "missing":
        return value is MISSING
    if op == "exists":
        return value is not MISSING
    if value is MISSING:
        return False
    expected = rule["value"]
    if op == "eq":
        return same_json(value, expected)
    if op == "ne":
        return not same_json(value, expected)
    if op == "length_eq":
        return isinstance(value, (dict, list, str)) and len(value) == expected
    if op == "contains":
        if isinstance(value, list):
            return any(same_json(item, expected) for item in value)
        if isinstance(value, (dict, str)) and isinstance(expected, str):
            return expected in value
    return False


def validate_cases(document):
    if (not isinstance(document, dict) or not {"schema_version", "cases"} <= set(document)
            or set(document) - {"schema_version", "cases"} - CONTRACT_METADATA
            or type(document["schema_version"]) is not int or document["schema_version"] != 1):
        fail("INVALID_CASE_CONTRACT")
    cases = document["cases"]
    if not isinstance(cases, list) or not 1 <= len(cases) <= 1000:
        fail("INVALID_CASE_CONTRACT")
    seen = set()
    for case in cases:
        if not isinstance(case, dict) or set(case) - {"id", "request", "assertions", "expected_http_status", "conversation_id", "turn"}:
            fail("INVALID_CASE_CONTRACT")
        identifier = case.get("id")
        if not isinstance(identifier, str) or not SAFE_ID.fullmatch(identifier) or identifier in seen:
            fail("INVALID_CASE_ID")
        seen.add(identifier)
        expected = case.get("expected_http_status", 200)
        if type(expected) is not int or not 100 <= expected <= 599:
            fail("INVALID_EXPECTED_HTTP_STATUS")
        rejection_case = 400 <= expected <= 499
        request = case.get("request")
        if (not isinstance(request, dict) or not {"question", "ledger_version"} <= set(request)
                or set(request) - {"question", "ledger_version", "context_entity_id"}
                or not isinstance(request["question"], str) or not (0 if rejection_case else 1) <= len(request["question"]) <= 2000
                or type(request["ledger_version"]) is not int
                or not (-2**63 if rejection_case else 1) <= request["ledger_version"] <= 2**63 - 1):
            fail("INVALID_QUERY_REQUEST")
        context = request.get("context_entity_id")
        if context is not None and (not isinstance(context, str) or not 1 <= len(context) <= 250):
            fail("INVALID_QUERY_REQUEST")
        assertions = case.get("assertions")
        if not isinstance(assertions, list) or not (0 if rejection_case else 1) <= len(assertions) <= 100:
            fail("INVALID_ASSERTION")
        for rule in assertions:
            validate_assertion(rule)
        if "conversation_id" in case and (not isinstance(case["conversation_id"], str) or not SAFE_ID.fullmatch(case["conversation_id"])):
            fail("INVALID_CONVERSATION_METADATA")
        if "turn" in case and (type(case["turn"]) is not int or case["turn"] < 1 or "conversation_id" not in case):
            fail("INVALID_CONVERSATION_METADATA")
    return cases


def validate_auth(auth, base_url):
    if not isinstance(auth, dict) or set(auth) != {"base_url", "session_token", "csrf_token", "identity_revision"} or auth["base_url"] != base_url:
        fail("AUTH_ORIGIN_MISMATCH")
    for key in ("session_token", "csrf_token", "identity_revision"):
        value = auth[key]
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,512}", value):
            fail("INVALID_AUTHENTICATION")
    return auth


def new_output_directory(path):
    try:
        requested = Path(path).expanduser()
        if requested.is_symlink():
            fail("PRIVATE_NEW_OUTPUT_REQUIRED")
        resolved = requested.resolve()
        if resolved.is_relative_to(REPO_ROOT) or not resolved.parent.is_dir():
            fail("PRIVATE_NEW_OUTPUT_REQUIRED")
        resolved.mkdir(mode=0o700)
        return resolved
    except (OSError, ValueError, TypeError):
        fail("PRIVATE_NEW_OUTPUT_REQUIRED")


def write_private(path, raw):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)


def json_bytes(document):
    return (json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def nearest_rank(values, percentile):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(percentile * len(ordered)) - 1)]


def distribution(rows):
    values = [row["latency_ns"] for row in rows]
    return {"samples": len(rows), "p50_ns": nearest_rank(values, .50), "p95_ns": nearest_rank(values, .95),
            "min_ns": min(values) if values else None, "max_ns": max(values) if values else None,
            "assertion_pass_samples": sum(row["verdict"] == "PASS" for row in rows)}


def aggregate(samples, cases):
    measured = [row for row in samples if row["phase"] == "measured"]
    verdicts = Counter(row["verdict"] for row in measured)
    return {"unique_cases": len(cases), "measured_samples": len(measured),
            "warmup_samples_excluded": len(samples) - len(measured),
            "measured_unique_cases": len({row["case_id"] for row in measured}),
            "case_pass_count": sum(any(row["case_id"] == case["id"] for row in measured)
                and all(row["verdict"] == "PASS" for row in measured if row["case_id"] == case["id"]) for case in cases),
            "verdicts": {key: verdicts[key] for key in ("PASS", "FAIL")},
            "latency": distribution(measured),
            "by_response_outcome": {kind: distribution([row for row in measured if row["response_outcome"] == kind])
                                    for kind in ("success", "hold", "failure")},
            "by_case": {case["id"]: {**distribution([row for row in measured if row["case_id"] == case["id"]]),
                "by_response_outcome": {kind: distribution([row for row in measured if row["case_id"] == case["id"] and row["response_outcome"] == kind])
                                        for kind in ("success", "hold", "failure")}} for case in cases}}


def response_outcome(status, body, error):
    if error or status is None or not 200 <= status < 300 or not isinstance(body, dict):
        return "failure"
    if body.get("status") == "ANSWERED":
        return "success"
    if body.get("status") in {"PARTIAL", "UNKNOWN", "NEEDS_CLARIFICATION"}:
        return "hold"
    return "failure"


def measure(client, base_url, request, headers, clock):
    raw, status, error = bytearray(), None, None
    started = clock()
    try:
        with client.stream("POST", base_url + ENDPOINT, json=request, headers=headers) as response:
            status = response.status_code
            for chunk in response.iter_bytes():
                if len(raw) + len(chunk) > MAX_RESPONSE_BYTES:
                    error = "RESPONSE_SIZE_LIMIT"
                    break
                raw.extend(chunk)
    except httpx.TimeoutException:
        error = "REQUEST_TIMEOUT"
    except httpx.HTTPError:
        error = "HTTP_TRANSPORT_ERROR"
    ended = clock()
    return {"raw": bytes(raw), "http_status": status, "transport_error": error,
            "latency_ns": max(0, ended - started)}


def evaluate(*, base_url, isolated, cases_raw, auth, output_dir, warmup=1, repetitions=5,
             timeout=30, expected_cases_sha256=None, transport=None, clock=monotonic_ns):
    valid_base_url(base_url)
    if isolated is not True:
        fail("ISOLATED_ATTESTATION_REQUIRED")
    if (type(warmup) is not int or not 0 <= warmup <= 100 or type(repetitions) is not int
            or not 1 <= repetitions <= 1000 or not isinstance(timeout, (int, float))
            or isinstance(timeout, bool) or not math.isfinite(timeout) or not 0 < timeout <= 120):
        fail("INVALID_EVALUATION_LIMIT")
    digest = hashlib.sha256(cases_raw).hexdigest()
    if expected_cases_sha256 is not None and expected_cases_sha256 != digest:
        fail("PREREGISTERED_CONTRACT_HASH_MISMATCH")
    contract = decode_json(cases_raw)
    cases = validate_cases(contract)
    for key, expected in (("warmup_per_case", warmup), ("repetitions", repetitions),
                          ("concurrency", 1), ("latency_percentile", "nearest_rank")):
        if key in contract and not same_json(contract[key], expected):
            fail("PREREGISTERED_SETTINGS_MISMATCH")
    validate_auth(auth, base_url)
    if len(cases) * (warmup + repetitions) > MAX_SAMPLES:
        fail("EVALUATION_SAMPLE_LIMIT")
    output = new_output_directory(output_dir)
    headers = {"Origin": base_url, "X-Requested-With": "MK-S2", "X-CSRF-Token": auth["csrf_token"],
               "X-Identity-Revision": auth["identity_revision"], "Cookie": "onto_session=" + auth["session_token"]}
    samples, stopped = [], None
    with httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False, transport=transport) as client:
        for phase, repetitions_count in (("warmup", warmup), ("measured", repetitions)):
            for repetition in range(repetitions_count):
                for case in cases:
                    index = len(samples) + 1
                    prefix = f"sample-{index:06d}"
                    write_private(output / (prefix + ".request.json"), json_bytes(case["request"]))
                    response = measure(client, base_url, case["request"], headers, clock)
                    raw = response.pop("raw")
                    write_private(output / (prefix + ".response.bin"), raw)
                    body = None
                    parse_error = None
                    try:
                        body = decode_json(raw)
                    except EvaluationError:
                        parse_error = "RESPONSE_JSON_INVALID"
                    checks = [check_assertion(body, rule) for rule in case["assertions"]] if parse_error is None else [False] * len(case["assertions"])
                    safety = None
                    if response["http_status"] is not None and 200 <= response["http_status"] < 300:
                        safety = (isinstance(body, dict) and type(body.get("model_calls")) is int and body["model_calls"] == 0
                                  and type(body.get("ledger_mutations")) is int and body["ledger_mutations"] == 0
                                  and body.get("execution_kind") == "deterministic_domain_query")
                    passed = (response["transport_error"] is None and parse_error is None
                              and response["http_status"] == case.get("expected_http_status", 200)
                              and all(checks) and safety is not False)
                    sample = {**response, "case_id": case["id"], "phase": phase, "repetition": repetition + 1,
                        "verdict": "PASS" if passed else "FAIL", "parse_error": parse_error,
                        "response_outcome": response_outcome(response["http_status"], body, response["transport_error"] or parse_error),
                        "assertion_results": checks, "read_only_response_contract": safety,
                        "request_file": prefix + ".request.json", "response_file": prefix + ".response.bin"}
                    for key in ("conversation_id", "turn"):
                        if key in case:
                            sample[key] = case[key]
                    samples.append(sample)
                    if safety is False:
                        stopped = "READ_ONLY_RESPONSE_CONTRACT_VIOLATION"
                        break
                if stopped:
                    break
            if stopped:
                break
    summary = aggregate(samples, cases)
    report = {"schema_version": 1, "status": "PASS" if not stopped and summary["verdicts"]["FAIL"] == 0 else "FAIL",
              "scope": "isolated_deterministic_query_api", "base_url": base_url, "endpoint": ENDPOINT,
              "cases_sha256": digest, "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "stop_reason": stopped, "planned_measured_samples": len(cases) * repetitions,
              "settings": {"warmup_per_case": warmup, "repetitions_per_case": repetitions, "timeout_seconds": timeout, "concurrency": 1},
              "metric": {"clock": "monotonic_ns", "start": "client_dispatch", "end": "complete_body_receipt_or_transport_failure",
                         "quantiles": "nearest_rank", "warmups_included": False, "failure_latencies_included": True},
              "limitations": ["ISOLATION_IS_OPERATOR_ATTESTED", "CONVERSATION_METADATA_IS_NOT_SERVER_MEMORY",
                               "NOT_MODEL_TTFT_OR_PROVIDER_QUALITY", "CPU_MEMORY_NOT_MEASURED", "NO_MODEL_BUDGET_CREATED"],
              "summary": summary, "samples": samples}
    write_private(output / "results.json", json_bytes(report))
    write_private(output / "aggregate.json", json_bytes({key: value for key, value in report.items() if key != "samples"}))
    return report


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        fail("EVALUATION_ARGUMENT_INVALID")


def main(argv=None):
    try:
        parser = SafeParser(description=__doc__)
        parser.add_argument("--base-url", required=True)
        parser.add_argument("--isolated", action="store_true", required=True)
        parser.add_argument("--cases", required=True)
        parser.add_argument("--auth-file")
        parser.add_argument("--output-dir", required=True)
        parser.add_argument("--warmup", type=int, default=1)
        parser.add_argument("--repetitions", type=int, default=5)
        parser.add_argument("--timeout", type=float, default=30)
        parser.add_argument("--expected-cases-sha256")
        args = parser.parse_args(argv)
        auth = decode_json(private_input(args.auth_file)) if args.auth_file else {
            key: os.environ.get("ONTO_EVAL_" + name, "") for key, name in (
                ("base_url", "BASE_URL"), ("session_token", "SESSION_TOKEN"),
                ("csrf_token", "CSRF_TOKEN"), ("identity_revision", "IDENTITY_REVISION"))}
        report = evaluate(base_url=args.base_url, isolated=args.isolated, cases_raw=private_input(args.cases),
            auth=auth, output_dir=args.output_dir, warmup=args.warmup, repetitions=args.repetitions, timeout=args.timeout,
            expected_cases_sha256=args.expected_cases_sha256)
        summary = report["summary"]
        print(json.dumps({"status": report["status"], "unique_cases": summary["unique_cases"],
            "measured_samples": summary["measured_samples"], "warmup_samples_excluded": summary["warmup_samples_excluded"],
            "pass_samples": summary["verdicts"]["PASS"], "fail_samples": summary["verdicts"]["FAIL"]}))
        return 0 if report["status"] == "PASS" else 1
    except EvaluationError as error:
        print(json.dumps({"status": "ERROR", "code": str(error)}))
        return 2
    except KeyboardInterrupt:
        print(json.dumps({"status": "ERROR", "code": "EVALUATION_INTERRUPTED"}))
        return 130
    except Exception:
        print(json.dumps({"status": "ERROR", "code": "EVALUATION_FAILED"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
