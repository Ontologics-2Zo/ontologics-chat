import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import httpx
from pydantic import ValidationError

from service.observability import (LangfuseConfig, LangfuseExporter,
                                   MetadataObserver, ObservationExportError,
                                   metadata_only, otel_span_id, trace_id)
from service.observability import (EXECUTION_ROOT_SPAN, ModelObservationInput,
    ModelObservationOutput, ObservedUsage, RouteFeatures, WorkflowObservationInput,
    WorkflowObservationOutput, safe_io)


class RecordingExporter:
    def __init__(self, fail=False):
        self.spans = []
        self.fail = fail

    def export(self, span):
        self.spans.append(span)
        if self.fail:
            raise RuntimeError("PRIVATE_PROVIDER_RESPONSE secret-value")

    def close(self):
        pass


class ObservabilityTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)

    def emit(self, observer, identity="attempt-1", parent="root", metadata=None, **changes):
        arguments = dict(execution_id="execution-1", span_id=identity,
                         parent_span_id=parent, name="model.route", kind="generation",
                         status="completed", metadata=metadata or {},
                         started_at=self.now, ended_at=self.now + timedelta(milliseconds=4))
        arguments.update(changes)
        return observer.emit_span(**arguments)

    def test_allowlist_drops_text_sources_credentials_nested_values_and_unknown_usage(self):
        raw = {"prompt": "PRIVATE_TEXT", "output": "PRIVATE_RESULT", "source_path": "/private/source",
               "api_key": "sk-secret", "error": "PRIVATE_ERROR", "metadata": {"secret": "x"},
               "model": "openai/model", "input_tokens": None, "cost_usd": None,
               "usage_unknown_reason": "provider_missing_usage", "error_code": "SAFE_ERROR"}
        self.assertEqual(metadata_only(raw), {"model": "openai/model",
                         "usage_unknown_reason": "provider_missing_usage", "error_code": "SAFE_ERROR"})
        self.assertEqual(metadata_only({"model": "sk-secret", "provider": "https://secret.invalid",
                                       "profile_id": "/private/path", "schema_version": {"x": 1}}), {})

    def test_canonical_decimal_is_not_rounded_or_recomputed(self):
        cost = "0.0000000000000000000123456789"
        clean = metadata_only({"cost_usd": Decimal(cost), "input_tokens": 2,
                               "output_tokens": 3, "total_tokens": None})
        self.assertEqual(Decimal(clean.pop("cost_usd")), Decimal(cost))
        self.assertEqual(clean, {"input_tokens": 2, "output_tokens": 3})
        self.assertEqual(metadata_only({"cost_usd": "NaN", "input_tokens": True}), {})

    def test_completed_parent_and_child_share_trace_and_parent_identity(self):
        exporter = RecordingExporter()
        observer = MetadataObserver(exporter)
        self.assertTrue(self.emit(observer, "root", None, kind="agent"))
        self.assertTrue(self.emit(observer, metadata={"attempt_id": "attempt-1"}))
        root, child = exporter.spans
        self.assertEqual(root["trace_id"], child["trace_id"])
        self.assertEqual(child["trace_id"], trace_id("execution-1"))
        self.assertEqual(child["parent_span_id"], root["span_id"])
        self.assertEqual(child["span_id"], otel_span_id("execution-1", "attempt-1"))
        observer.close()

    def test_durable_receipt_prevents_same_attempt_double_export_after_restart(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "receipts.sqlite3"
            first = RecordingExporter(); observer = MetadataObserver(first, receipt_path=path)
            self.assertTrue(self.emit(observer, metadata={"cost_usd": "0.2"}))
            self.assertFalse(self.emit(observer, metadata={"cost_usd": "0.2"}))
            observer.close()
            second = RecordingExporter(); observer = MetadataObserver(second, receipt_path=path)
            self.assertFalse(self.emit(observer, metadata={"cost_usd": "0.2"}))
            self.assertEqual(len(observer.receipts.records()), 1)
            self.assertEqual(second.spans, [])
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            observer.close()

    def test_exporter_failure_is_isolated_and_ambiguous_delivery_is_not_replayed(self):
        exporter = RecordingExporter(fail=True); observer = MetadataObserver(exporter)
        self.assertFalse(self.emit(observer))
        self.assertEqual(observer.failed, 1)
        self.assertFalse(self.emit(observer))
        self.assertEqual(len(exporter.spans), 1)
        row = observer.receipts.records()[0]
        self.assertEqual(row["export_state"], "failed_or_unknown")
        self.assertNotIn("PRIVATE_PROVIDER_RESPONSE", json.dumps(row))
        observer.close()

    def test_local_only_does_not_enable_langsmith_from_unrelated_key(self):
        self.assertIsNone(LangfuseConfig.from_env({"ONTO_LANGSMITH_API_KEY": "private"}))
        observer = MetadataObserver()
        self.assertTrue(self.emit(observer))
        self.assertFalse(observer.langsmith_enabled)
        self.assertEqual(observer.receipts.records()[0]["export_state"], "local_only")
        observer.close()

    def test_endpoint_validation_refuses_cloud_credentials_urls_and_conflicting_hosts(self):
        for base in ["https://cloud.langfuse.com", "http://127.0.0.1@remote.invalid",
                     "http://user:pass@127.0.0.1", "http://127.0.0.1/?key=secret",
                     "http://127.0.0.1/elsewhere"]:
            with self.subTest(base=base), self.assertRaises(ValueError):
                LangfuseConfig(base, "public", "secret")
        with self.assertRaises(ValueError):
            LangfuseConfig.from_env({"LANGFUSE_PUBLIC_KEY": "p", "LANGFUSE_SECRET_KEY": "s",
                                    "LANGFUSE_BASE_URL": "http://localhost:1", "LANGFUSE_HOST": "http://localhost:2"})
        self.assertNotIn("private-secret", repr(LangfuseConfig("http://127.0.0.1:53000", "p", "private-secret")))

    def test_otel_json_has_parent_timing_and_metadata_but_no_raw_io_or_price_inference(self):
        requests = []
        def handler(request):
            requests.append(request)
            return httpx.Response(200, json={})
        exporter = LangfuseExporter(LangfuseConfig("http://127.0.0.1:53000", "p", "s"),
                                    transport=httpx.MockTransport(handler))
        observer = MetadataObserver(exporter)
        self.assertTrue(self.emit(observer, metadata={"model": "openai/model", "cost_usd": None, "ledger_version": 2,
                        "prompt": "PRIVATE", "output": "PRIVATE", "attempt_id": "attempt-1"}))
        request = requests[0]
        self.assertEqual(request.url.path, "/api/public/otel/v1/traces")
        self.assertEqual(request.headers["x-langfuse-ingestion-version"], "4")
        body = json.loads(request.content)["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
        attributes = {a["key"]: a["value"] for a in body["attributes"]}
        self.assertEqual(body["parentSpanId"], otel_span_id("execution-1", "root"))
        self.assertGreater(int(body["endTimeUnixNano"]), int(body["startTimeUnixNano"]))
        self.assertNotIn("PRIVATE", request.content.decode())
        self.assertFalse(any(k.endswith(("input", "output", "model.name")) for k in attributes))
        self.assertEqual(attributes["langfuse.observation.metadata.ledger_version"], {"stringValue": "2"})
        observer.close()

    def test_http_failure_partial_rejection_and_redirects_are_not_silent_success(self):
        for status, body in [(500, {"secret": "PRIVATE"}), (302, {}),
                             (200, {"partialSuccess": {"rejectedSpans": "1"}}),
                             (200, {"successes": [{}], "errors": [{"message": "PRIVATE_ERROR"}]}),
                             (207, {"errors": [{"message": "PRIVATE_ERROR"}]})]:
            with self.subTest(status=status):
                exporter = LangfuseExporter(LangfuseConfig("http://127.0.0.1", "p", "s"),
                    transport=httpx.MockTransport(lambda request: httpx.Response(status, json=body)))
                observer = MetadataObserver(exporter)
                self.assertFalse(self.emit(observer))
                self.assertEqual(observer.failed, 1)
                observer.close()

    def test_invalid_timing_and_untrusted_event_fields_do_not_escape_callback(self):
        observer = MetadataObserver(RecordingExporter())
        self.assertFalse(self.emit(observer, started_at=datetime(2026, 1, 1)))
        self.assertFalse(self.emit(observer, ended_at=self.now - timedelta(seconds=1)))
        self.assertFalse(observer({"metadata": "PRIVATE", "prompt": "secret"}))
        self.assertTrue(observer({"execution_id": "execution-1", "span_id": "node-1",
                                  "name": "workflow.node", "status": "completed", "metadata": "PRIVATE"}))
        self.assertNotIn("PRIVATE", json.dumps(observer.receipts.records()))
        observer.close()

    def workflow_input(self, stage="execution", **updates):
        return WorkflowObservationInput(stage=stage, requested_capability=None,
            ledger_version=3, projection_version=3, **updates)

    def workflow_output(self, stage="execution", **updates):
        values = dict(stage=stage, status="completed", capability="material_analysis",
                      tool_calls=1, segment_count=2, result_status="PARTIAL")
        values.update(updates)
        return WorkflowObservationOutput(**values)

    def test_model_io_contains_actual_features_capability_and_nullable_usage(self):
        observer = MetadataObserver(content_enabled=True)
        self.addCleanup(observer.close)
        model_input = ModelObservationInput(features=RouteFeatures.from_features(("materials", "inventory")),
            execution_mode="fake", max_output_tokens=128, retry=False)
        model_output = ModelObservationOutput(status="completed", capability="material_analysis",
            dispatched=True, usage=ObservedUsage(source="fake", unknown_reason="FAKE_NOT_BILLABLE"))
        self.assertTrue(self.emit(observer, input=model_input, output=model_output))
        span = observer.receipts.records()[0]["span"]
        self.assertEqual({k for k, v in span["input"]["features"].items() if v}, {"materials", "inventory"})
        self.assertEqual(span["output"]["capability"], "material_analysis")
        self.assertIsNone(span["output"]["usage"]["cost_usd"])
        self.assertIsNone(span["output"]["usage"]["total_tokens"])
        self.assertEqual(span["input"]["content_scope"], "structured_only")

    def test_untyped_extra_nested_and_forged_io_are_rejected_not_serialized(self):
        observer = MetadataObserver(content_enabled=True)
        self.addCleanup(observer.close)
        self.assertFalse(self.emit(observer, input={"question": "PRIVATE"}))
        with self.assertRaises(ValidationError):
            WorkflowObservationInput(stage="execution", requested_capability=None,
                ledger_version=1, projection_version=1, source_document="PRIVATE")
        with self.assertRaises(ValueError):
            RouteFeatures.from_features(("PRIVATE",))
        forged = self.workflow_input().model_copy(update={"ledger_version": True})
        self.assertFalse(self.emit(observer, input=forged))
        self.assertEqual(observer.receipts.records(), [])

    def test_content_default_off_and_evaluation_override_even_explicit_true(self):
        for options in ({}, {"content_enabled": True, "purpose": "evaluation"}):
            observer = MetadataObserver(**options)
            try:
                self.assertTrue(self.emit(observer, input=self.workflow_input(question="PRIVATE_QUESTION"),
                    output=self.workflow_output(answer="PRIVATE_ANSWER", conclusion="PRIVATE_GOLD")))
                encoded = json.dumps(observer.receipts.records())
                self.assertNotIn("PRIVATE_", encoded)
                self.assertIn("structured_only", encoded)
            finally:
                observer.close()

    def test_authorized_workflow_content_redacts_credentials_paths_and_bounds_length(self):
        observer = MetadataObserver(content_enabled=True, redact_values=("synthetic-opaque-secret",))
        self.addCleanup(observer.close)
        question = "가용 재고? sk-test123456 api_key=hidden Bearer private-token /private/folder synthetic-opaque-secret"
        self.assertTrue(self.emit(observer, input=self.workflow_input(question=question),
            output=self.workflow_output(answer="확정 답변 " + "가" * 7000)))
        span = observer.receipts.records()[0]["span"]
        self.assertTrue(span["input"]["question"].startswith("가용 재고?"))
        for secret in ("sk-test123456", "hidden", "private-token", "/private/folder", "synthetic-opaque-secret"):
            self.assertNotIn(secret, json.dumps(span))
        self.assertTrue(span["output"]["answer"].endswith("[TRUNCATED]"))
        self.assertLessEqual(len(span["output"]["answer"]), 6020)

    def test_only_terminal_root_sets_legacy_trace_io_child_cannot_overwrite(self):
        bodies = []
        def handler(request):
            bodies.append(json.loads(request.content)["resourceSpans"][0]["scopeSpans"][0]["spans"][0])
            return httpx.Response(200, json={})
        observer = MetadataObserver(LangfuseExporter(LangfuseConfig("http://127.0.0.1:53000", "p", "s"),
                                     transport=httpx.MockTransport(handler)), content_enabled=True)
        self.addCleanup(observer.close)
        self.assertTrue(self.emit(observer, "segment-1", EXECUTION_ROOT_SPAN, kind="chain", status="waiting",
            input=self.workflow_input("segment", question="업무 질문"),
            output=self.workflow_output("segment", status="waiting", clarification="확인해 주세요")))
        self.assertTrue(self.emit(observer, EXECUTION_ROOT_SPAN, None, kind="agent", terminal_summary=True,
            input=self.workflow_input(question="명확해진 질문"), output=self.workflow_output(answer="확정 응답")))
        self.assertFalse(self.emit(observer, "segment-2", EXECUTION_ROOT_SPAN, terminal_summary=True,
            input=self.workflow_input(), output=self.workflow_output()))
        waiting, root = [{a["key"]: a["value"]["stringValue"] for a in body["attributes"]} for body in bodies]
        self.assertNotIn("langfuse.trace.input", waiting)
        self.assertEqual(json.loads(waiting["langfuse.observation.output"])["status"], "waiting")
        self.assertEqual(root["langfuse.trace.input"], root["langfuse.observation.input"])
        self.assertEqual(json.loads(root["langfuse.trace.output"])["answer"], "확정 응답")
        self.assertEqual(bodies[0]["parentSpanId"], bodies[1]["spanId"])

    def test_content_redacts_prefixed_assignments_generic_credential_uri_and_jwt(self):
        sensitive = ["OTHER_API_KEY=opaque-secret-one", '"ONTO_OTHER_SECRET_KEY":"opaque-secret-two"',
                     "DATABASE_URL=postgresql://synthetic:private-pass@localhost:1/db",
                     "bolt://synthetic:private-pass@localhost:2", "redis://:private-pass@localhost:3",
                     "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJwcml2YXRlIn0.synthetic_signature"]
        value = safe_io(self.workflow_input(question="허용 질문 " + " ".join(sensitive)), content_enabled=True)
        self.assertTrue(value["question"].startswith("허용 질문"))
        for secret in ("opaque-secret-one", "opaque-secret-two", "private-pass", "synthetic_signature"):
            self.assertNotIn(secret, value["question"])

    def test_terminal_root_is_immutable_and_unknown_cost_is_not_zero(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "receipts.sqlite3"
            observer = MetadataObserver(receipt_path=path)
            self.assertTrue(self.emit(observer, EXECUTION_ROOT_SPAN, None, terminal_summary=True,
                input=self.workflow_input(), output=self.workflow_output()))
            observer.close()
            observer = MetadataObserver(receipt_path=path)
            self.assertFalse(self.emit(observer, EXECUTION_ROOT_SPAN, None, terminal_summary=True,
                input=self.workflow_input(), output=self.workflow_output(segment_count=3)))
            self.assertEqual(observer.receipts.records()[0]["span"]["output"]["segment_count"], 2)
            observer.close()
        unknown = ModelObservationOutput(status="outcome_unknown", capability=None, dispatched=True,
            error_code="MODEL_PROVIDER_TIMEOUT", usage=ObservedUsage(source="unavailable", unknown_reason="USAGE_NOT_RETURNED"))
        self.assertIsNone(safe_io(unknown)["usage"]["cost_usd"])
        exact = ObservedUsage(cost_usd=Decimal("0.0000000000000000000123456789"), source="provider")
        self.assertEqual(Decimal(safe_io(unknown.model_copy(update={"usage": exact}))["usage"]["cost_usd"]), exact.cost_usd)


if __name__ == "__main__":
    unittest.main()
