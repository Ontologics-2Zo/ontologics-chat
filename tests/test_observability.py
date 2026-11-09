import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import httpx

from service.observability import (LangfuseConfig, LangfuseExporter,
                                   MetadataObserver, ObservationExportError,
                                   metadata_only, otel_span_id, trace_id)


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


if __name__ == "__main__":
    unittest.main()
