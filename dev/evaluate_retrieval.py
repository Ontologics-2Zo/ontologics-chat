#!/usr/bin/env python3
"""Measure snapshot-local retrieval quality (R0/R1/R3) without model calls.

Private case JSON (outside Git):
{"schema_version": 1, "ledger_version": 9, "cases": [
  {"id": "case-1", "group": "exact_id", "query": "...", "context_entity_id": null,
   "relevant": [{"kind": "ledger_entity", "text": "unique substring"}]}]}

Each relevant matcher names one document by its kind and a substring of its
indexed text. A hit is relevant when it matches any matcher of the case.
Labels are written by the operator from the snapshot, never from search output.

Only POST /api/mks2/retrieval/search is used, as a local development account
on an isolated server; port 8870 (the original service) is refused. Raw
responses and per-case rows go to a new private directory. Stdout carries
aggregate numbers and fixed codes only, never queries, documents or paths.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import re
import stat
from time import monotonic_ns
from urllib.parse import urlsplit

import httpx

ENDPOINT = "/api/mks2/retrieval/search"
PROFILES = ("R0", "R1", "R3", "R4")
KINDS = {"ledger_entity", "source_row", "application_review", "development_document"}
SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}\Z")
GROUP = re.compile(r"[a-z][a-z0-9_]{0,39}\Z")


class EvaluationError(Exception):
    """Only fixed, public-safe codes cross the CLI boundary."""


def fail(code):
    raise EvaluationError(code)


def valid_base_url(value):
    parsed = urlsplit(value)
    if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"} or not parsed.port
            or parsed.path not in {"", "/"} or parsed.query or parsed.fragment or parsed.username):
        fail("BASE_URL_NOT_LOCAL")
    if parsed.port == 8870:
        fail("ORIGINAL_SERVICE_REFUSED")
    return f"http://{parsed.hostname}:{parsed.port}"


def validate_cases(document):
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        fail("CASES_SCHEMA_INVALID")
    version = document.get("ledger_version")
    cases = document.get("cases")
    if type(version) is not int or version < 1 or not isinstance(cases, list) or not 1 <= len(cases) <= 500:
        fail("CASES_SCHEMA_INVALID")
    seen = set()
    for case in cases:
        if (not isinstance(case, dict) or not isinstance(case.get("id"), str) or not SAFE_ID.fullmatch(case["id"])
                or case["id"] in seen or not isinstance(case.get("group"), str) or not GROUP.fullmatch(case["group"])
                or not isinstance(case.get("query"), str) or not 0 < len(case["query"].strip()) <= 1000
                or not isinstance(case.get("relevant"), list) or not 1 <= len(case["relevant"]) <= 20):
            fail("CASE_INVALID")
        context = case.get("context_entity_id")
        if context is not None and (not isinstance(context, str) or not 0 < len(context) <= 500):
            fail("CASE_INVALID")
        for matcher in case["relevant"]:
            if (not isinstance(matcher, dict) or set(matcher) != {"kind", "text"} or matcher["kind"] not in KINDS
                    or not isinstance(matcher["text"], str) or not 0 < len(matcher["text"]) <= 200):
                fail("CASE_INVALID")
        seen.add(case["id"])
    return version, cases


def matches(hit, matcher):
    return hit.get("kind") == matcher["kind"] and matcher["text"] in (hit.get("text") or "")


def score_case(hits, relevant, k):
    """Binary relevance metrics over ranked hits; one matcher is one relevant document."""
    found_at = []  # first rank (1-based) at which each matcher is satisfied, or None
    for matcher in relevant:
        found_at.append(next((rank for rank, hit in enumerate(hits, 1) if matches(hit, matcher)), None))
    flags = [any(matches(hit, m) for m in relevant) for hit in hits]
    top = flags[:k]
    first = next((rank for rank, flag in enumerate(flags, 1) if flag), None)
    dcg = sum(1 / math.log2(rank + 1) for rank, flag in enumerate(top, 1) if flag)
    ideal = sum(1 / math.log2(rank + 1) for rank in range(1, min(len(relevant), k) + 1))
    total = len(relevant)
    return {"recall_at_k": sum(1 for r in found_at if r is not None and r <= k) / total,
            "recall_at_limit": sum(1 for r in found_at if r is not None) / total,
            "precision_at_k": sum(top) / k,
            "reciprocal_rank": 1 / first if first else 0.0,
            "ndcg_at_k": dcg / ideal if ideal else 0.0,
            "hit_at_k": 1.0 if any(top) else 0.0,
            "relevant_total": total, "hits_returned": len(hits)}


METRICS = ("recall_at_k", "recall_at_limit", "precision_at_k", "reciprocal_rank", "ndcg_at_k", "hit_at_k")


def nearest_rank(values, percentile):
    ordered = sorted(values)
    return ordered[max(0, math.ceil(percentile / 100 * len(ordered)) - 1)] if ordered else None


def aggregate(rows):
    scored = [row for row in rows if row["status"] in {"HIT", "NO_HIT"}]
    result = {"cases": len(rows), "scored": len(scored),
              "status": {s: sum(1 for row in rows if row["status"] == s) for s in sorted({row["status"] for row in rows})}}
    for metric in METRICS:
        result[metric] = round(sum(row["metrics"][metric] for row in scored) / len(scored), 4) if scored else None
    latency = [row["latency_ms"] for row in rows]
    result["latency_ms"] = {"p50": nearest_rank(latency, 50), "p95": nearest_rank(latency, 95)}
    return result


def new_output_directory(path):
    directory = Path(path).expanduser()
    if directory.exists():
        fail("OUTPUT_DIRECTORY_EXISTS")
    directory.mkdir(parents=True, mode=0o700)
    return directory


def write_private(path, document):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, stat.S_IRUSR | stat.S_IWUSR)
    with os.fdopen(fd, "wb") as handle:
        handle.write(json.dumps(document, ensure_ascii=False, sort_keys=True, indent=1).encode())


class Session:
    def __init__(self, base_url, profile_id, timeout):
        self.client = httpx.Client(base_url=base_url, timeout=timeout, trust_env=False,
                                   headers={"Origin": base_url, "X-Requested-With": "MK-S2"})
        response = self.client.post("/api/mks2/session", json={"profile_id": profile_id})
        if response.status_code != 200:
            fail("SESSION_UNAVAILABLE")
        body = response.json()
        self.headers = {"X-CSRF-Token": body["csrf_token"], "X-Identity-Revision": body["principal"]["identity_revision"]}

    def search(self, body):
        start = monotonic_ns()
        response = self.client.post(ENDPOINT, json=body, headers=self.headers)
        elapsed = round((monotonic_ns() - start) / 1e6, 2)
        try:
            payload = response.json()
        except ValueError:
            payload = None
        return response.status_code, payload, elapsed

    def close(self):
        self.client.close()


def evaluate(session, version, cases, profiles, *, k, limit):
    rows = []
    for profile in profiles:
        for case in cases:
            body = {"query": case["query"], "profile": profile, "limit": limit, "ledger_version": version}
            if case.get("context_entity_id"):
                body["context_entity_id"] = case["context_entity_id"]
            status_code, payload, latency = session.search(body)
            data = payload.get("data", payload) if isinstance(payload, dict) else {}
            status = data.get("status") if status_code == 200 and isinstance(data, dict) else f"HTTP_{status_code}"
            hits = data.get("hits", []) if status in {"HIT", "NO_HIT"} else []
            if status in {"HIT", "NO_HIT"} and data.get("meta", {}).get("ledger_version") not in (None, version):
                status = "VERSION_MISMATCH"
            rows.append({"profile": profile, "case_id": case["id"], "group": case["group"], "status": status,
                         "latency_ms": latency, "metrics": score_case(hits, case["relevant"], k),
                         "hit_modes": [hit.get("match_modes") for hit in hits], "raw": payload})
    return rows


def summarize(rows, profiles):
    summary = {}
    for profile in profiles:
        selected = [row for row in rows if row["profile"] == profile]
        groups = sorted({row["group"] for row in selected})
        summary[profile] = {"all": aggregate(selected),
                            "groups": {group: aggregate([row for row in selected if row["group"] == group]) for group in groups}}
    return summary


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        print("ARGUMENTS_INVALID", flush=True)
        raise SystemExit(2)


def main(argv=None):
    parser = SafeParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--isolated", action="store_true", required=True)
    parser.add_argument("--cases", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--dev-profile", default="dev-operator-a")
    parser.add_argument("--profiles", default=",".join(PROFILES))
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=60)
    args = parser.parse_args(argv)
    try:
        base_url = valid_base_url(args.base_url)
        profiles = tuple(args.profiles.split(","))
        if not profiles or any(p not in PROFILES for p in profiles) or not 1 <= args.k <= args.limit <= 20:
            fail("ARGUMENTS_INVALID")
        try:
            version, cases = validate_cases(json.loads(Path(args.cases).expanduser().read_text()))
        except (OSError, ValueError):
            fail("CASES_UNREADABLE")
        output = new_output_directory(args.output_dir)
        session = Session(base_url, args.dev_profile, args.timeout)
        try:
            rows = evaluate(session, version, cases, profiles, k=args.k, limit=args.limit)
        finally:
            session.close()
        summary = summarize(rows, profiles)
        write_private(output / "rows.json", rows)
        write_private(output / "summary.json", {"k": args.k, "limit": args.limit, "ledger_version": version,
                                                "summary": summary})
        print(json.dumps({"k": args.k, "limit": args.limit, "summary": summary}, sort_keys=True), flush=True)
    except EvaluationError as error:
        print(str(error), flush=True)
        raise SystemExit(1)
    except httpx.HTTPError:
        print("SERVICE_UNREACHABLE", flush=True)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
