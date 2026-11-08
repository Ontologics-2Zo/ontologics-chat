"""Metadata-only local observability, separate from domain and cost accounting.

Langfuse receives completed OTLP/HTTP JSON spans, never prompts, answers, source
documents, exceptions or credentials. The ModelStore remains the sole canonical
usage ledger; this module only mirrors its already-known attempt metadata.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import sqlite3
from threading import Lock
from typing import Any, Mapping
from urllib.parse import urlsplit

import httpx


_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,159}\Z")
_ERROR = re.compile(r"[A-Z][A-Z0-9_]{0,79}\Z")
_STRINGS = frozenset({
    "execution_id", "attempt_id", "segment_id", "capability", "execution_mode",
    "provider", "model", "profile_id", "workflow_version", "agent_version",
    "prompt_version", "schema_version", "policy_version", "context_revision",
    "ontology_version", "rule_version", "usage_source", "usage_unknown_reason",
    "stage", "status", "event", "error_code",
})
_COUNTS = frozenset({"input_tokens", "output_tokens", "total_tokens",
                     "ledger_version", "projection_version", "tool_calls"})
_KINDS = frozenset({"span", "generation", "event", "agent", "tool", "chain",
                    "retriever", "guardrail", "evaluator"})
_STATES = frozenset({"completed", "failed", "needs_review", "cancelled", "ok",
                     "success", "error", "interrupted", "waiting", "unknown"})


def _label(value: Any) -> str | None:
    if (not isinstance(value, str) or not _LABEL.fullmatch(value)
            or "://" in value or value.startswith(("sk-", "pk-", "Bearer"))):
        return None
    return value


def metadata_only(values: Mapping[str, Any]) -> dict[str, Any]:
    """Positive allowlist; unknown fields and all nested/free-form values vanish."""
    clean: dict[str, Any] = {}
    for key, value in values.items():
        if key in _STRINGS:
            if key == "error_code":
                if isinstance(value, str) and _ERROR.fullmatch(value):
                    clean[key] = value
            elif (safe := _label(value)) is not None:
                clean[key] = safe
        elif key in _COUNTS and type(value) is int and 0 <= value <= 2**53 - 1:
            clean[key] = value
        elif (key == "cost_usd" and type(value) in (str, int, Decimal)
              and len(str(value)) <= 100):
            try:
                amount = Decimal(value)
                if amount.is_finite() and 0 <= amount <= Decimal("1000000"):
                    clean[key] = str(amount)
            except InvalidOperation:
                pass
    return clean


def _local_url(value: str) -> str:
    parsed = urlsplit(value)
    host = parsed.hostname
    try:
        local = host == "localhost" or ipaddress.ip_address(host or "").is_loopback
    except ValueError:
        local = False
    if (parsed.scheme not in {"http", "https"} or not local
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in {"", "/"}):
        raise ValueError("OBSERVABILITY_LOCAL_ENDPOINT_REQUIRED")
    return value.rstrip("/")


@dataclass(frozen=True)
class LangfuseConfig:
    base_url: str
    public_key: str = field(repr=False)
    secret_key: str = field(repr=False)
    timeout_seconds: float = 3.0

    def __post_init__(self):
        object.__setattr__(self, "base_url", _local_url(self.base_url))
        if not self.public_key or not self.secret_key or not 0 < self.timeout_seconds <= 30:
            raise ValueError("OBSERVABILITY_CONFIGURATION_INVALID")

    @classmethod
    def from_env(cls, environment: Mapping[str, str] | None = None):
        env = os.environ if environment is None else environment
        public, secret = env.get("LANGFUSE_PUBLIC_KEY"), env.get("LANGFUSE_SECRET_KEY")
        if not public and not secret:
            return None
        base, host = env.get("LANGFUSE_BASE_URL"), env.get("LANGFUSE_HOST")
        if base and host and base.rstrip("/") != host.rstrip("/"):
            raise ValueError("OBSERVABILITY_ENDPOINT_CONFLICT")
        if not public or not secret or not (base or host):
            raise ValueError("OBSERVABILITY_CONFIGURATION_INCOMPLETE")
        return cls(base or host, public, secret)


class ObservationExportError(Exception):
    """A fixed code only: response bodies, URLs and auth are never exposed."""


def _attribute(key: str, value: Any) -> dict[str, Any]:
    # String metadata is valid OTLP and avoids the installed v3.100 parser's
    # intValue-string -> NaN bug. Typed canonical values remain in the local
    # sink/ModelStore; the observer does not perform arithmetic on these values.
    return {"key": key, "value": {"stringValue": str(value)}}


class LangfuseExporter:
    """Supported OTLP JSON transport; no redirects, env proxies or cloud fallback.

    References: https://langfuse.com/integrations/native/opentelemetry
    https://langfuse.com/integrations/native/opentelemetry/migration-to-v4
    """
    def __init__(self, config: LangfuseConfig, *, transport=None):
        self.config = config
        self._client = httpx.Client(
            base_url=config.base_url, auth=(config.public_key, config.secret_key),
            timeout=config.timeout_seconds, follow_redirects=False, trust_env=False,
            transport=transport,
        )

    def export(self, span: dict[str, Any]) -> None:
        attributes = [
            _attribute("langfuse.observation.type", span["kind"]),
            _attribute("langfuse.trace.name", "ontologics.execution"),
            _attribute("langfuse.environment", "local-development"),
            _attribute("langfuse.observation.metadata.status", span["status"]),
        ]
        for key, value in span["metadata"].items():
            attributes.append(_attribute("langfuse.observation.metadata." + key, value))
        # Do not set a model billing attribute: Langfuse must not infer prices
        # that differ from (or fill in UNKNOWN in) the canonical ModelStore.
        body = {
            "traceId": span["trace_id"], "spanId": span["span_id"],
            "name": span["name"], "kind": 1,
            "startTimeUnixNano": str(span["start_ns"]),
            "endTimeUnixNano": str(span["end_ns"]), "attributes": attributes,
            "status": {"code": 2 if span["status"] in {"failed", "error"} else 1},
        }
        if span["parent_span_id"]:
            body["parentSpanId"] = span["parent_span_id"]
        payload = {"resourceSpans": [{
            "resource": {"attributes": [_attribute("service.name", "ontologics-chat")]},
            "scopeSpans": [{"scope": {"name": "ontologics.metadata", "version": "1"},
                            "spans": [body]}],
        }]}
        try:
            response = self._client.post("/api/public/otel/v1/traces", json=payload,
                                         headers={"x-langfuse-ingestion-version": "4"})
            if not 200 <= response.status_code < 300:
                raise ObservationExportError("OBSERVABILITY_EXPORT_FAILED")
            result = response.json() if response.content else {}
            # v3 returns HTTP 200/207 with per-event errors even for OTLP.
            if result.get("errors"):
                raise ObservationExportError("OBSERVABILITY_EXPORT_REJECTED")
            partial = result.get("partialSuccess", {})
            if int(partial.get("rejectedSpans", 0)):
                raise ObservationExportError("OBSERVABILITY_EXPORT_REJECTED")
        except ObservationExportError:
            raise
        except Exception:
            raise ObservationExportError("OBSERVABILITY_EXPORT_FAILED") from None

    def close(self):
        self._client.close()


class SpanReceipts:
    """Metadata local sink and durable at-most-once export claims.

    A failed/ambiguous delivery is retained without automatic replay: the server
    may have accepted it before a timeout, and re-ingestion can double-count.
    This is not the canonical model usage or business ledger.
    """
    def __init__(self, path: str | Path | None = None):
        target = ":memory:" if path is None else str(path)
        self._connection = sqlite3.connect(target, check_same_thread=False)
        if path is not None:
            os.chmod(path, 0o600)
        self._lock = Lock()
        with self._connection:
            self._connection.execute("""CREATE TABLE IF NOT EXISTS observation_spans (
                trace_id TEXT NOT NULL, span_id TEXT NOT NULL, payload TEXT NOT NULL,
                export_state TEXT NOT NULL, PRIMARY KEY(trace_id, span_id))""")

    def claim(self, span: dict[str, Any]) -> bool:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "INSERT OR IGNORE INTO observation_spans VALUES (?, ?, ?, 'claimed')",
                (span["trace_id"], span["span_id"], json.dumps(span, sort_keys=True)),
            )
            return cursor.rowcount == 1

    def outcome(self, span: dict[str, Any], state: str):
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE observation_spans SET export_state=? WHERE trace_id=? AND span_id=?",
                (state, span["trace_id"], span["span_id"]),
            )

    def records(self) -> list[dict[str, Any]]:
        with self._lock:
            return [{"span": json.loads(row[0]), "export_state": row[1]} for row in
                    self._connection.execute("SELECT payload, export_state FROM observation_spans")]

    def close(self):
        self._connection.close()


def _time_ns(value: datetime | int | float | str | None) -> int:
    if value is None:
        value = datetime.now(timezone.utc)
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("OBSERVABILITY_TIMEZONE_REQUIRED")
        delta = value.astimezone(timezone.utc) - datetime(1970, 1, 1, tzinfo=timezone.utc)
        return ((delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds) * 1000
    if type(value) not in (int, float) or not 0 <= value <= 253402300799:
        raise ValueError("OBSERVABILITY_TIME_INVALID")
    return int(Decimal(str(value)) * 1000000000)


def trace_id(execution_id: str) -> str:
    return hashlib.sha256(("execution:" + execution_id).encode()).hexdigest()[:32]


def otel_span_id(execution_id: str, identity: str) -> str:
    return hashlib.sha256(("span:" + execution_id + ":" + identity).encode()).hexdigest()[:16]


class MetadataObserver:
    """Best-effort sink: observability errors never change a domain outcome."""
    def __init__(self, exporter=None, *, receipt_path: str | Path | None = None):
        self.exporter = exporter
        self.receipts = SpanReceipts(receipt_path)
        self.exported = 0
        self.failed = 0
        self.duplicates = 0
        self.invalid = 0
        self.langsmith_enabled = False

    def emit_span(self, execution_id, span_id, parent_span_id, name, kind, status,
                  metadata, started_at, ended_at):
        try:
            if (not _label(execution_id) or not _label(span_id)
                    or (parent_span_id is not None and not _label(parent_span_id))
                    or not _label(name) or kind not in _KINDS or status not in _STATES):
                self.invalid += 1
                return False
            clean = metadata_only(metadata if isinstance(metadata, Mapping) else {})
            clean["execution_id"] = execution_id
            start, end = _time_ns(started_at), _time_ns(ended_at)
            if end < start:
                self.invalid += 1
                return False
            span = {"trace_id": trace_id(execution_id),
                    "span_id": otel_span_id(execution_id, span_id),
                    "parent_span_id": otel_span_id(execution_id, parent_span_id)
                    if parent_span_id else None,
                    "name": name, "kind": kind, "status": status,
                    "metadata": clean, "start_ns": start, "end_ns": end}
            if not self.receipts.claim(span):
                self.duplicates += 1
                return False
            if self.exporter is None:
                self.receipts.outcome(span, "local_only")
                return True
            try:
                self.exporter.export(span)
            except Exception:
                self.failed += 1
                self.receipts.outcome(span, "failed_or_unknown")
                return False
            self.exported += 1
            self.receipts.outcome(span, "exported")
            return True
        except Exception:
            self.invalid += 1
            return False

    def __call__(self, event: Mapping[str, Any]):
        """Adapter for Host-generated completed workflow events, never contents."""
        if not isinstance(event, Mapping):
            return False
        nested = event.get("metadata")
        values = dict(nested) if isinstance(nested, Mapping) else {}
        values.update({key: event[key] for key in _STRINGS | _COUNTS if key in event})
        return self.emit_span(
            event.get("execution_id"), event.get("span_id") or event.get("node_id"),
            event.get("parent_span_id"), event.get("name", "workflow.node"),
            event.get("kind", "span"), event.get("status", "completed"), values,
            event.get("started_at"), event.get("ended_at"),
        )

    def close(self):
        self.receipts.close()
        if self.exporter:
            self.exporter.close()
