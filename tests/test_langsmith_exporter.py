"""Synthetic metadata only; no credentials, original DB, or network required."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from threading import Event
import time
import unittest
from uuid import uuid4

import httpx
from pydantic import ValidationError

from service.agent_config import load_agent_settings
from service.errors import ServiceError
from service.langsmith_exporter import (LangSmithConfig, LangSmithMetadataExporter,
    MetadataObservation, TraceMetadata, approved_endpoint, run_id)
from service.observability import (MetadataObserver, WorkflowObservationInput,
    WorkflowObservationOutput)


def observation(span="root", parent=None, **metadata):
    return MetadataObservation(execution_id="synthetic-execution", span_id=span, parent_span_id=parent,
        name="model.route" if span == "attempt" else "agent_segment", kind="chain", status="completed",
        start_ns=1700000000000000000, end_ns=1700000000100000000,
        metadata=TraceMetadata(execution_id="synthetic-execution", **metadata))


class LocalSink:
    def __init__(self, fail=False):
        self.spans, self.fail = [], fail

    def export(self, span):
        self.spans.append(span)
        if self.fail:
            raise RuntimeError("synthetic-local-only-error")

    def close(self):
        pass


class LangSmithExporterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "metadata.sqlite3"
        self.config = LangSmithConfig(api_key="synthetic-private-key", project_id=str(uuid4()))
        self.requests = []

    def exporter(self, handler=None, **overrides):
        def record(request):
            self.requests.append(request)
            return handler(request) if handler else httpx.Response(202)
        result = LangSmithMetadataExporter(overrides.pop("config", self.config),
            receipt_path=overrides.pop("path", self.path), transport=httpx.MockTransport(record))
        self.addCleanup(result.close)
        return result

    def test_explicit_config_and_no_key_only_auto_enable(self):
        result = load_agent_settings(environ={"ONTO_LANGSMITH_API_KEY": "synthetic-private-key",
                                             "LANGSMITH_TRACING": "true"})
        self.assertFalse(result.langsmith_enabled)
        self.assertNotIn("synthetic-private-key", repr(result))
        disabled = load_agent_settings(environ={"ONTO_MODEL_MODE": "disabled"})
        self.assertFalse(disabled.langsmith_enabled)
        values = {"ONTO_LANGSMITH_ENABLED": "true", "ONTO_LANGSMITH_API_KEY": "synthetic-private-key",
                  "ONTO_LANGSMITH_PROJECT_ID": self.config.project_id}
        self.assertTrue(load_agent_settings(environ=values).public()["remote_observability_enabled"])
        for change in ({"ONTO_LANGSMITH_ENABLED": "yes"}, {"ONTO_LANGSMITH_API_KEY": ""},
                       {"ONTO_LANGSMITH_PROJECT_ID": "new-project-name"},
                       {"ONTO_LANGSMITH_WORKSPACE_ID": "invalid"},
                       {"ONTO_LANGSMITH_ENDPOINT": "https://private.invalid"}):
            with self.subTest(change=change), self.assertRaises(ServiceError):
                load_agent_settings(environ={**values, **change})

    def test_endpoint_allowlist_excludes_credential_redirect_and_proxy_destinations(self):
        self.assertEqual(approved_endpoint("https://api.smith.langchain.com/"), self.config.endpoint)
        for value in ("http://api.smith.langchain.com", "https://api.smith.langchain.com.evil.invalid",
                      "https://secret@api.smith.langchain.com", "https://api.smith.langchain.com:443",
                      "https://api.smith.langchain.com/runs", "https://api.smith.langchain.com?key=x",
                      "https://api.smith.langchain.com/#x", "http://127.0.0.1:1"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                approved_endpoint(value)
        exporter = self.exporter(lambda req: httpx.Response(307, headers={"location": "https://private.invalid"}))
        self.assertTrue(exporter.submit(observation()))
        self.assertTrue(exporter.flush())
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(exporter.summary()["states"], {"failed_or_unknown": 1})

    def test_typed_envelope_rejects_io_extra_fields_and_forged_model_instances(self):
        exporter = self.exporter()
        self.assertFalse(exporter.submit(observation().model_dump()))
        with self.assertRaises(ValidationError):
            TraceMetadata(execution_id="e", question="synthetic-private-question")
        for changes in ({"input": {"question": "secret"}}, {"start_ns": 2**64},
                        {"span_id": "sk-secret"}, {"name": "private-question"}):
            forged = observation().model_copy(update=changes)
            self.assertFalse(exporter.submit(forged))
        self.assertEqual(self.requests, [])

    def test_unknown_usage_remains_null_and_no_inferred_billing_fields(self):
        exporter = self.exporter()
        self.assertTrue(exporter.submit(observation(usage_source="unavailable", usage_unknown_reason="USAGE_NOT_RETURNED")))
        self.assertTrue(exporter.flush())
        value = json.loads(self.requests[0].content)
        self.assertEqual(value["inputs"], {})
        self.assertEqual(value["outputs"], {})
        self.assertEqual(value["run_type"], "chain")
        self.assertEqual(set(value["extra"]), {"metadata"})
        self.assertIsNone(value["extra"]["metadata"]["cost_usd"])
        self.assertIsNone(value["extra"]["metadata"]["total_tokens"])
        self.assertNotIn("usage_metadata", value)
        for cost in ("NaN", "-1", "private-text", "Infinity"):
            with self.subTest(cost=cost), self.assertRaises(ValidationError):
                TraceMetadata(execution_id="e", cost_usd=cost)

    def test_exact_canonical_cost_and_parent_order_survive_waiting_restart(self):
        first = self.exporter()
        self.assertTrue(first.submit(observation("attempt", "segment", cost_usd="0.0000000123456789", attempt_id="attempt")))
        self.assertTrue(first.submit(observation("segment", "root")))
        self.assertFalse(first.flush(.03))
        self.assertEqual(self.requests, [])
        first.close(.01)
        second = self.exporter()
        self.assertTrue(second.submit(observation()))
        self.assertTrue(second.flush())
        values = [json.loads(req.content) for req in self.requests]
        self.assertEqual([x["id"] for x in values], [run_id("synthetic-execution", x) for x in ("root", "segment", "attempt")])
        root, segment, child = values
        self.assertEqual(child["parent_run_id"], segment["id"])
        self.assertEqual(segment["parent_run_id"], root["id"])
        self.assertEqual(child["trace_id"], root["id"])
        self.assertTrue(child["dotted_order"].startswith(segment["dotted_order"] + "."))
        self.assertEqual(child["extra"]["metadata"]["cost_usd"], "0.0000000123456789")
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_timeout_is_not_retried_across_restart_or_duplicate_submission(self):
        def timeout(req):
            raise httpx.ReadTimeout("synthetic-private-response", request=req)
        first = self.exporter(timeout)
        self.assertTrue(first.submit(observation()))
        self.assertTrue(first.flush())
        self.assertFalse(first.submit(observation()))
        first.close()
        second = self.exporter()
        self.assertFalse(second.submit(observation()))
        self.assertTrue(second.flush())
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(second.summary()["states"], {"failed_or_unknown": 1})
        with sqlite3.connect(self.path) as db:
            raw = str(db.execute("SELECT * FROM langsmith_exports").fetchall())
        self.assertNotIn("synthetic-private-response", raw)
        self.assertNotIn(self.config.api_key, raw)

    def test_existing_claim_is_never_resent_after_process_loss(self):
        first = self.exporter()
        self.assertTrue(first.submit(observation("child", "missing")))
        first.close(.01)
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE langsmith_exports SET state='claimed'")
        second = self.exporter()
        self.assertFalse(second.submit(observation("child", "missing")))
        self.assertTrue(second.flush())
        self.assertEqual(self.requests, [])
        self.assertEqual(second.summary()["states"], {"claimed": 1})

    def test_queue_capacity_and_nonblocking_submission_bounded_close(self):
        blocked, entered = Event(), Event()
        def handler(req):
            entered.set()
            blocked.wait(2)
            return httpx.Response(202)
        exporter = self.exporter(handler, config=LangSmithConfig(api_key=self.config.api_key,
            project_id=self.config.project_id, max_pending=2))
        self.addCleanup(blocked.set)
        start = time.monotonic()
        self.assertTrue(exporter.submit(observation()))
        self.assertLess(time.monotonic() - start, .2)
        self.assertTrue(entered.wait(1))
        self.assertTrue(exporter.submit(observation("child1", "root")))
        self.assertTrue(exporter.submit(observation("child2", "root")))
        self.assertFalse(exporter.submit(observation("child3", "root")))
        start = time.monotonic()
        exporter.close(.02)
        self.assertLess(time.monotonic() - start, .3)
        blocked.set()
        exporter._thread.join(1)
        self.assertEqual(exporter.summary()["states"].get("dropped_capacity"), 1)
        self.assertEqual(len(self.requests), 1)

    def test_different_destination_receipts_are_independent(self):
        first = self.exporter()
        first.submit(observation()); self.assertTrue(first.flush()); first.close()
        second = self.exporter(config=LangSmithConfig(api_key=self.config.api_key, project_id=str(uuid4())))
        self.assertTrue(second.submit(observation()))
        self.assertTrue(second.flush())
        self.assertEqual(len(self.requests), 2)

    def test_late_root_recovers_saturated_waiting_queue_and_future_trace_exports(self):
        exporter = self.exporter(config=LangSmithConfig(api_key=self.config.api_key,
            project_id=self.config.project_id, max_pending=2))
        self.assertTrue(exporter.submit(observation("child1", "root")))
        self.assertTrue(exporter.submit(observation("child2", "root")))
        self.assertFalse(exporter.flush(.02))
        self.assertTrue(exporter.submit(observation()))
        self.assertTrue(exporter.flush())
        self.assertEqual(exporter.summary()["states"], {"exported": 1, "dropped_capacity": 2})
        self.assertTrue(exporter.submit(observation("next-root")))
        self.assertTrue(exporter.flush())
        self.assertEqual(len(self.requests), 2)

    def test_duplicate_root_never_evicts_pending_rows(self):
        exporter = self.exporter(config=LangSmithConfig(api_key=self.config.api_key,
            project_id=self.config.project_id, max_pending=2))
        self.assertTrue(exporter.submit(observation()))
        self.assertTrue(exporter.flush())
        self.assertTrue(exporter.submit(observation("child1", "future-root")))
        self.assertTrue(exporter.submit(observation("child2", "future-root")))
        self.assertFalse(exporter.submit(observation()))
        self.assertEqual(exporter.summary()["states"], {"exported": 1, "queued": 2})
        self.assertEqual(len(self.requests), 1)

    def test_dropped_parent_descendants_terminate_instead_of_blocking_queue(self):
        exporter = self.exporter(config=LangSmithConfig(api_key=self.config.api_key,
            project_id=self.config.project_id, max_pending=1))
        self.assertTrue(exporter.submit(observation("waiting", "root")))
        self.assertFalse(exporter.submit(observation("lost-parent", "root")))
        self.assertTrue(exporter.submit(observation()))
        self.assertTrue(exporter.flush())
        self.assertTrue(exporter.submit(observation("child", "lost-parent")))
        self.assertTrue(exporter.flush())
        self.assertTrue(exporter.submit(observation("grandchild", "child")))
        self.assertTrue(exporter.flush())
        self.assertEqual(exporter.summary()["states"], {"exported": 1, "dropped_capacity": 2, "dropped_parent": 2})
        self.assertTrue(exporter.submit(observation("future-root")))
        self.assertTrue(exporter.flush())
        self.assertEqual(len(self.requests), 2)

    def test_remote_receipts_independent_of_local_failure_and_duplicates(self):
        local = LocalSink(fail=True)
        first = MetadataObserver(local, receipt_path=Path(self.tmp.name) / "local.sqlite3")
        args = dict(execution_id="synthetic-execution", span_id="root", parent_span_id=None,
            name="agent_segment", kind="chain", status="completed", metadata={},
            started_at=datetime.now(timezone.utc), ended_at=None)
        self.assertFalse(first.emit_span(**args)); first.close()
        remote = self.exporter()
        second = MetadataObserver(LocalSink(), receipt_path=Path(self.tmp.name) / "local.sqlite3", langsmith_exporter=remote)
        self.addCleanup(second.close)
        self.assertFalse(second.emit_span(**args))
        self.assertTrue(second.flush())
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(second.duplicates, 1)
        self.assertEqual(second.exporter.spans, [])

    def test_local_full_content_never_enters_remote_queue_even_when_remote_fails(self):
        remote = self.exporter(lambda req: httpx.Response(503))
        local = LocalSink()
        observer = MetadataObserver(local, content_enabled=True, langsmith_exporter=remote)
        self.addCleanup(observer.close)
        self.assertTrue(observer.emit_span("synthetic-execution", "root", None, "agent_segment", "chain", "completed",
            {"question": "PRIVATE_SYNTHETIC_QUESTION", "source": "PRIVATE_SYNTHETIC_SOURCE", "cost_usd": None},
            datetime.now(timezone.utc), None,
            input=WorkflowObservationInput(stage="segment", requested_capability=None,
                ledger_version=1, projection_version=1, question="PRIVATE_SYNTHETIC_QUESTION"),
            output=WorkflowObservationOutput(stage="segment", status="completed", capability=None,
                tool_calls=0, segment_count=1, answer="PRIVATE_SYNTHETIC_ANSWER")))
        self.assertTrue(observer.flush())
        self.assertIn("PRIVATE_SYNTHETIC_QUESTION", json.dumps(local.spans))
        wire = self.requests[0].content.decode()
        with sqlite3.connect(self.path) as db:
            queued = str(db.execute("SELECT payload FROM langsmith_exports").fetchall())
        for value in (wire, queued):
            self.assertNotIn("PRIVATE_SYNTHETIC", value)
            self.assertNotIn("question", value)
            self.assertNotIn("answer", value)
        self.assertEqual(observer.exported, 1)
        self.assertEqual(observer.failed, 0)
        self.assertEqual(observer.langsmith_status()["states"], {"failed_or_unknown": 1})

    def test_known_secret_as_allowed_label_is_rejected_only_remotely(self):
        remote = self.exporter()
        local = LocalSink()
        observer = MetadataObserver(local, langsmith_exporter=remote, redact_values=("opaque-key-value",))
        self.addCleanup(observer.close)
        self.assertTrue(observer.emit_span("synthetic-execution", "root", None, "agent_segment", "chain", "completed",
            {"model": "opaque-key-value"}, datetime.now(timezone.utc), None))
        self.assertTrue(observer.flush())
        self.assertEqual(self.requests, [])
        self.assertEqual(observer.langsmith_invalid, 1)
        self.assertEqual(observer.exported, 1)
