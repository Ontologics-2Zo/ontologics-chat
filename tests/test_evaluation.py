"""Synthetic HTTP doubles only; no service, DB, provider or private source input."""
from contextlib import redirect_stdout
from decimal import Decimal
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

from dev import evaluate_service as evaluation


BASE = "http://localhost:8999"
AUTH = {"base_url": BASE, "session_token": "synthetic-session-secret", "csrf_token": "synthetic-csrf-secret",
        "identity_revision": "synthetic-revision"}


def case(identifier="case-1", **extra):
    return {"id": identifier, "request": {"question": "Synthetic independent test question", "ledger_version": 7},
            "assertions": [{"path": "/status", "op": "eq", "value": "ANSWERED"}], **extra}


def contract(cases=None, **extra):
    return json.dumps({"schema_version": 1, "cases": cases or [case()], **extra}).encode()


def answer(status="ANSWERED", **extra):
    return {"status": status, "model_calls": 0, "ledger_mutations": 0,
            "execution_kind": "deterministic_domain_query", **extra}


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def run_evaluation(self, handler, *, raw=None, times=None, **extra):
        iterator = iter(times or range(100000))
        return evaluation.evaluate(base_url=BASE, isolated=True, cases_raw=raw or contract(), auth=AUTH,
            output_dir=self.root / "results", warmup=0, repetitions=1,
            transport=httpx.MockTransport(handler), clock=lambda: next(iterator), **extra)

    def test_assertions_distinguish_missing_null_booleans_and_exact_json_numbers(self):
        data = evaluation.decode_json(b'{"number":1.000000000000000001,"flag":true,"null":null,"a/b":{"~x":["safe"]}}')
        rules = [
            {"path": "/number", "op": "eq", "value": Decimal("1.000000000000000001")},
            {"path": "/number", "op": "ne", "value": 1},
            {"path": "/flag", "op": "ne", "value": 1},
            {"path": "/null", "op": "exists"}, {"path": "/absent", "op": "missing"},
            {"path": "/a~1b/~0x/0", "op": "contains", "value": "af"},
            {"path": "/a~1b/~0x", "op": "length_eq", "value": 1},
        ]
        for rule in rules:
            evaluation.validate_assertion(rule)
            self.assertTrue(evaluation.check_assertion(data, rule))
        self.assertFalse(evaluation.check_assertion(data, {"path": "/null", "op": "missing"}))
        self.assertFalse(evaluation.check_assertion(data, {"path": "/missing", "op": "ne", "value": 0}))
        self.assertFalse(evaluation.check_assertion(data, {"path": "/a~1b/~0x/01", "op": "exists"}))

    def test_invalid_contracts_and_executable_or_conversation_request_fields_are_rejected(self):
        for raw in (b'{"schema_version":1,"schema_version":1,"cases":[]}', b'{"n":NaN}'):
            with self.assertRaises(evaluation.EvaluationError): evaluation.decode_json(raw)
        for extra in ({"endpoint": "/api/mks2/agent/executions"}, {"conversation": []}, {"actor_id": "injected"}):
            row = case(); row["request"].update(extra)
            with self.assertRaisesRegex(evaluation.EvaluationError, "INVALID_QUERY_REQUEST"):
                evaluation.validate_cases(evaluation.decode_json(contract([row])))
        for rule in ({"path": "/x", "op": "eval", "value": "code"}, {"path": "/~2", "op": "exists"},
                     {"path": "/x", "op": "exists", "value": True}):
            with self.assertRaises(evaluation.EvaluationError): evaluation.validate_assertion(rule)

    def test_origins_main_port_auth_mismatch_and_header_injection_fail_before_dispatch(self):
        for url in ("https://localhost:8999", "http://0.0.0.0:8999", "http://remote.invalid:8999",
                    "http://localhost:8870", "http://127.0.0.1:8870", "http://localhost:8999/", "http://user@localhost:8999"):
            with self.assertRaises(evaluation.EvaluationError): evaluation.valid_base_url(url)
        for auth in ({**AUTH, "base_url": "http://localhost:8998"}, {**AUTH, "session_token": "value; other=secret"},
                     {**AUTH, "csrf_token": "bad\r\nheader"}):
            with self.assertRaises(evaluation.EvaluationError): evaluation.validate_auth(auth, BASE)
        with self.assertRaisesRegex(evaluation.EvaluationError, "ISOLATED_ATTESTATION_REQUIRED"):
            evaluation.evaluate(base_url=BASE, isolated=False, cases_raw=contract(), auth=AUTH, output_dir=self.root / "unused")
        self.assertFalse((self.root / "unused").exists())

    def test_negative_contract_cases_reach_server_only_with_expected_4xx_and_exact_status_check(self):
        invalid = case(expected_http_status=422, assertions=[])
        invalid["request"]["ledger_version"] = 0
        received = []
        def handler(request):
            received.append(json.loads(request.content))
            return httpx.Response(422, json={"error": {"code": "INVALID_REQUEST"}})
        report = self.run_evaluation(handler, raw=contract([invalid]))
        self.assertEqual(received[0]["ledger_version"], 0)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["samples"][0]["response_outcome"], "failure")
        invalid["expected_http_status"] = 200
        with self.assertRaises(evaluation.EvaluationError):
            evaluation.validate_cases(evaluation.decode_json(contract([invalid])))
        normal = case(assertions=[])
        with self.assertRaises(evaluation.EvaluationError):
            evaluation.validate_cases(evaluation.decode_json(contract([normal])))

    def test_private_files_and_new_outputs_reject_repository_symlinks_and_overwrites(self):
        source = self.root / "cases.json"; source.write_bytes(contract()); source.chmod(0o644)
        with self.assertRaises(evaluation.EvaluationError): evaluation.private_input(source)
        source.chmod(0o600); self.assertEqual(evaluation.private_input(source), contract())
        link = self.root / "alias.json"; link.symlink_to(source)
        with self.assertRaises(evaluation.EvaluationError): evaluation.private_input(link)
        with self.assertRaises(evaluation.EvaluationError): evaluation.new_output_directory(self.root)
        with patch.object(evaluation, "REPO_ROOT", self.root.resolve()):
            with self.assertRaises(evaluation.EvaluationError): evaluation.new_output_directory(self.root / "inside-repo")
            with self.assertRaises(evaluation.EvaluationError): evaluation.private_input(source)

    def test_query_only_wire_contract_raw_preservation_and_no_auth_artifacts(self):
        received = []
        def handler(request):
            received.append(request)
            self.assertEqual((request.method, str(request.url)), ("POST", BASE + evaluation.ENDPOINT))
            self.assertEqual(request.headers["origin"], BASE)
            self.assertEqual(request.headers["cookie"], "onto_session=" + AUTH["session_token"])
            self.assertEqual(request.headers["x-csrf-token"], AUTH["csrf_token"])
            self.assertEqual(request.headers["x-identity-revision"], AUTH["identity_revision"])
            self.assertEqual(json.loads(request.content), case()["request"])
            return httpx.Response(200, json=answer())
        raw = contract([case(conversation_id="group-one", turn=1)])
        report = self.run_evaluation(handler, raw=raw, times=[100, 5100])
        self.assertEqual(report["status"], "PASS"); self.assertEqual(len(received), 1)
        self.assertEqual(report["samples"][0]["latency_ns"], 5000)
        self.assertEqual(report["samples"][0]["conversation_id"], "group-one")
        output = self.root / "results"
        self.assertEqual(output.stat().st_mode & 0o777, 0o700)
        for path in output.iterdir():
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertNotIn(AUTH["session_token"].encode(), path.read_bytes())
            self.assertNotIn(AUTH["csrf_token"].encode(), path.read_bytes())
        self.assertEqual(json.loads((output / "sample-000001.response.bin").read_bytes()), answer())

    def test_nearest_rank_excludes_warmups_and_includes_expected_failure_latency(self):
        cases = [case("success"), case("hold", assertions=[{"path": "/status", "op": "eq", "value": "UNKNOWN"}]),
                 case("failure", expected_http_status=422, assertions=[{"path": "/error/code", "op": "eq", "value": "INVALID_REQUEST"}])]
        seen = 0
        def handler(request):
            nonlocal seen
            choice = seen % 3; seen += 1
            return httpx.Response(422, json={"error": {"code": "INVALID_REQUEST"}}) if choice == 2 else httpx.Response(200, json=answer("UNKNOWN" if choice else "ANSWERED"))
        durations = [900, 900, 900, 10, 20, 30, 40, 50, 60]
        clock = iter(value for index, duration in enumerate(durations) for value in (index * 10000, index * 10000 + duration))
        report = evaluation.evaluate(base_url=BASE, isolated=True, cases_raw=contract(cases), auth=AUTH,
            output_dir=self.root / "results", warmup=1, repetitions=2,
            transport=httpx.MockTransport(handler), clock=lambda: next(clock))
        summary = report["summary"]
        self.assertEqual((summary["unique_cases"], summary["measured_samples"], summary["warmup_samples_excluded"]), (3, 6, 3))
        self.assertEqual((summary["latency"]["p50_ns"], summary["latency"]["p95_ns"]), (30, 60))
        self.assertEqual(summary["by_response_outcome"]["failure"]["samples"], 2)
        self.assertEqual(summary["case_pass_count"], 3)
        self.assertEqual(summary["verdicts"], {"PASS": 6, "FAIL": 0})

    def test_timeout_and_invalid_json_remain_measured_failures(self):
        counter = 0
        def handler(request):
            nonlocal counter
            counter += 1
            if counter == 1: raise httpx.ReadTimeout("synthetic-private-error", request=request)
            return httpx.Response(503, content=b'not json')
        report = self.run_evaluation(handler, raw=contract([case("one"), case("two")]), times=[0, 40, 100, 160])
        self.assertEqual(report["summary"]["latency"]["p95_ns"], 60)
        self.assertEqual(report["summary"]["by_response_outcome"]["failure"]["samples"], 2)
        self.assertEqual(report["samples"][0]["transport_error"], "REQUEST_TIMEOUT")
        self.assertNotIn("synthetic-private-error", json.dumps(report))

    def test_redirects_are_not_followed_and_cannot_forward_session_credentials(self):
        calls = []
        def handler(request):
            calls.append(str(request.url)); return httpx.Response(302, headers={"Location": "https://remote.invalid/collect"})
        report = self.run_evaluation(handler)
        self.assertEqual(calls, [BASE + evaluation.ENDPOINT]); self.assertEqual(report["status"], "FAIL")

    def test_claimed_model_or_business_effect_stops_subsequent_dispatches(self):
        calls = []
        def handler(request):
            calls.append(request); return httpx.Response(200, json=answer(model_calls=1))
        report = self.run_evaluation(handler, raw=contract([case("one"), case("two")]))
        self.assertEqual(len(calls), 1)
        self.assertEqual(report["stop_reason"], "READ_ONLY_RESPONSE_CONTRACT_VIOLATION")
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["summary"]["case_pass_count"], 0)
        self.assertEqual(report["summary"]["measured_unique_cases"], 1)

    def test_preregistered_metadata_is_non_executable_and_must_match_measurement_settings(self):
        raw = contract(source="synthetic-independent-contract", metadata={"untrusted": "not executed"},
                       repetitions=1, warmup_per_case=0, concurrency=1, latency_percentile="nearest_rank")
        report = self.run_evaluation(lambda _: httpx.Response(200, json=answer()), raw=raw)
        self.assertEqual(report["cases_sha256"], evaluation.hashlib.sha256(raw).hexdigest())
        with self.assertRaisesRegex(evaluation.EvaluationError, "PREREGISTERED_SETTINGS_MISMATCH"):
            evaluation.evaluate(base_url=BASE, isolated=True, cases_raw=raw, auth=AUTH,
                output_dir=self.root / "wrong-settings", warmup=1, repetitions=1)
        with self.assertRaisesRegex(evaluation.EvaluationError, "PREREGISTERED_CONTRACT_HASH_MISMATCH"):
            evaluation.evaluate(base_url=BASE, isolated=True, cases_raw=raw, auth=AUTH,
                output_dir=self.root / "wrong-hash", expected_cases_sha256="0" * 64)

    def test_cli_failure_never_prints_credentials_questions_or_local_paths(self):
        stream = io.StringIO()
        with redirect_stdout(stream):
            code = evaluation.main(["--base-url", BASE, "--isolated", "--cases", str(self.root / "private-missing"),
                "--auth-file", str(self.root / "secret-missing"), "--output-dir", str(self.root / "output")])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(stream.getvalue()), {"status": "ERROR", "code": "PRIVATE_INPUT_REQUIRED"})
        self.assertNotIn(str(self.root), stream.getvalue())


if __name__ == "__main__":
    unittest.main()
