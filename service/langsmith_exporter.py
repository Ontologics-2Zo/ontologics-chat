"""Explicit metadata-only LangSmith export; no SDK auto tracing or model calls.

Completed observations enter a bounded durable queue, then one POST /runs per
destination/observation. An uncertain request is never replayed. Children wait
for their completed parent; WAITING segments remain local until a terminal root
exists. https://docs.langchain.com/langsmith/trace-with-api
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from threading import Event, Lock, Thread
import time
from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import UUID, uuid5

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


ENDPOINTS = frozenset({"https://api.smith.langchain.com", "https://eu.api.smith.langchain.com",
                      "https://apac.api.smith.langchain.com", "https://aws.api.smith.langchain.com"})
_NAMESPACE = UUID("e9fbc7f3-05a5-4bdf-b064-b5adc0c6b0ac")
Label = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,159}$")]
Count = Annotated[int, Field(ge=0, le=2**53 - 1)]


def approved_endpoint(value: str) -> str:
    parsed = urlsplit(value)
    if (value.rstrip("/") not in ENDPOINTS or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.port not in {None, 443}
            or parsed.path not in {"", "/"}):
        raise ValueError("LANGSMITH_ENDPOINT_NOT_APPROVED")
    return value.rstrip("/")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, revalidate_instances="always")


class TraceMetadata(_Strict):
    execution_id: Label
    attempt_id: Label | None = None
    segment_id: Label | None = None
    capability: Literal["fulfillment_read", "material_analysis", "product_review", "procurement_review", "needs_clarification"] | None = None
    execution_mode: Literal["fake", "live"] | None = None
    provider: Label | None = None
    model: Label | None = None
    profile_id: Label | None = None
    workflow_version: Label | None = None
    agent_version: Label | None = None
    prompt_version: Label | None = None
    schema_version: Label | None = None
    policy_version: Label | None = None
    context_revision: Label | None = None
    ontology_version: Label | None = None
    rule_version: Label | None = None
    usage_source: Literal["fake", "provider", "unavailable"] | None = None
    usage_unknown_reason: Label | None = None
    stage: Literal["execution", "segment", "route", "specialist", "validate"] | None = None
    status: Label | None = None
    event: Label | None = None
    error_code: Label | None = None
    input_tokens: Count | None = None
    output_tokens: Count | None = None
    total_tokens: Count | None = None
    ledger_version: Count | None = None
    projection_version: Count | None = None
    tool_calls: Count | None = None
    cost_usd: str | None = None

    @field_validator("*")
    @classmethod
    def no_secret_labels(cls, value):
        if isinstance(value, str) and ("://" in value or value.startswith(("sk-", "pk-", "lsv2_", "Bearer"))):
            raise ValueError("LANGSMITH_METADATA_LABEL_INVALID")
        return value

    @field_validator("cost_usd")
    @classmethod
    def canonical_cost(cls, value):
        if value is not None:
            try:
                valid = len(value) <= 100 and Decimal(value).is_finite() and 0 <= Decimal(value) <= 1000000
            except InvalidOperation:
                valid = False
            if not valid:
                raise ValueError("LANGSMITH_CANONICAL_COST_INVALID")
        return value


class MetadataObservation(_Strict):
    execution_id: Label
    span_id: Label
    parent_span_id: Label | None
    name: Literal["agent_execution_summary", "agent_segment", "route", "specialist", "validate", "model.route",
                  "workflow.node", "ledger.query", "material.requirements", "product.review", "procurement.options",
                  "retrieval.search", "retrieval.evidence", "tool.gateway", "fulfillment_read", "material_analysis",
                  "product_review", "procurement_review"]
    kind: Literal["span", "generation", "event", "agent", "tool", "chain", "retriever", "guardrail", "evaluator"]
    status: Literal["completed", "failed", "needs_review", "cancelled", "ok", "success", "error", "interrupted", "waiting", "unknown"]
    start_ns: int = Field(ge=0, le=2**63 - 1)
    end_ns: int = Field(ge=0, le=2**63 - 1)
    metadata: TraceMetadata

    @model_validator(mode="after")
    def consistent(self):
        if self.end_ns < self.start_ns or self.execution_id != self.metadata.execution_id or self.span_id == self.parent_span_id:
            raise ValueError("LANGSMITH_OBSERVATION_INVALID")
        for value in (self.execution_id, self.span_id, self.parent_span_id):
            if value is not None:
                TraceMetadata.no_secret_labels(value)
        return self


@dataclass(frozen=True)
class LangSmithConfig:
    api_key: str = field(repr=False)
    project_id: str
    endpoint: str = "https://api.smith.langchain.com"
    workspace_id: str | None = None
    timeout_seconds: float = 3.0
    max_pending: int = 256

    def __post_init__(self):
        object.__setattr__(self, "endpoint", approved_endpoint(self.endpoint))
        UUID(self.project_id)
        if self.workspace_id:
            UUID(self.workspace_id)
        if not self.api_key or not 0 < self.timeout_seconds <= 5 or not 1 <= self.max_pending <= 4096:
            raise ValueError("LANGSMITH_CONFIGURATION_INVALID")

    @property
    def destination(self):
        return hashlib.sha256(json.dumps([self.endpoint, self.project_id, self.workspace_id]).encode()).hexdigest()


def run_id(execution_id: str, span_id: str) -> str:
    return str(uuid5(_NAMESPACE, execution_id + ":" + span_id))


def _time(ns):
    seconds, rest = divmod(ns, 1000000000)
    return datetime.fromtimestamp(seconds, timezone.utc).replace(microsecond=rest // 1000)


class LangSmithMetadataExporter:
    """A private SQLite metadata queue and one bounded asynchronous HTTP worker."""
    def __init__(self, config: LangSmithConfig, *, receipt_path: str | Path, transport=None):
        self.config = config
        self.invalid = 0
        self._lock, self._wake, self._stop = Lock(), Event(), Event()
        self._busy = False
        self._closed = False
        self._last_states = {}
        self._db = sqlite3.connect(str(receipt_path), check_same_thread=False, timeout=0.05)
        os.chmod(receipt_path, 0o600)
        with self._db:
            self._db.execute("""CREATE TABLE IF NOT EXISTS langsmith_exports (
                destination TEXT NOT NULL, execution_id TEXT NOT NULL, span_id TEXT NOT NULL,
                parent_span_id TEXT, start_ns INTEGER NOT NULL, payload TEXT NOT NULL,
                state TEXT NOT NULL, PRIMARY KEY(destination,execution_id,span_id))""")
            self._db.execute("CREATE INDEX IF NOT EXISTS langsmith_queue ON langsmith_exports(destination,state,start_ns)")
        headers = {"x-api-key": config.api_key}
        if config.workspace_id:
            headers["x-tenant-id"] = config.workspace_id
        self._client = httpx.Client(base_url=config.endpoint, headers=headers, timeout=config.timeout_seconds,
                                    trust_env=False, follow_redirects=False, transport=transport)
        self._thread = Thread(target=self._work, name="ontologics-metadata-export", daemon=True)
        self._thread.start()
        self._wake.set()

    def submit(self, observation: MetadataObservation) -> bool:
        try:
            if type(observation) is not MetadataObservation or self._closed:
                raise ValueError("LANGSMITH_TYPED_METADATA_REQUIRED")
            if (set(observation.__dict__) - set(MetadataObservation.model_fields)
                    or type(observation.metadata) is not TraceMetadata
                    or set(observation.metadata.__dict__) - set(TraceMetadata.model_fields)):
                raise ValueError("LANGSMITH_TYPED_METADATA_REQUIRED")
            value = MetadataObservation.model_validate(observation.model_dump())
            encoded = value.model_dump_json()
            if len(encoded.encode()) > 16384 or self.config.api_key in encoded:
                raise ValueError("LANGSMITH_METADATA_INVALID")
            with self._lock, self._db:
                self._db.execute("BEGIN IMMEDIATE")
                count = self._db.execute("SELECT count(*) FROM langsmith_exports WHERE destination=? AND state='queued'",
                                         (self.config.destination,)).fetchone()[0]
                state = "queued" if count < self.config.max_pending else "dropped_capacity"
                cursor = self._db.execute("INSERT OR IGNORE INTO langsmith_exports VALUES (?,?,?,?,?,?,?)",
                    (self.config.destination, value.execution_id, value.span_id, value.parent_span_id, value.start_ns, encoded, state))
            self._wake.set()
            return bool(cursor.rowcount) and state == "queued"
        except Exception:
            self.invalid += 1
            return False

    def _next(self):
        with self._lock, self._db:
            self._db.execute("BEGIN IMMEDIATE")
            pending = self._db.execute("SELECT execution_id,span_id,payload FROM langsmith_exports WHERE destination=? AND state='queued' ORDER BY start_ns LIMIT ?",
                                       (self.config.destination, self.config.max_pending)).fetchall()
            for execution, identity, encoded in pending:
                observation = MetadataObservation.model_validate_json(encoded)
                chain, seen = [observation], {identity}
                parent = observation.parent_span_id
                while parent is not None and len(chain) <= 32:
                    if parent in seen:
                        break
                    seen.add(parent)
                    row = self._db.execute("SELECT payload,state FROM langsmith_exports WHERE destination=? AND execution_id=? AND span_id=?",
                                           (self.config.destination, execution, parent)).fetchone()
                    if row is None or row[1] not in {"exported", "failed_or_unknown"}:
                        break
                    ancestor = MetadataObservation.model_validate_json(row[0])
                    chain.append(ancestor)
                    parent = ancestor.parent_span_id
                if parent is not None:
                    continue
                self._db.execute("UPDATE langsmith_exports SET state='claimed' WHERE destination=? AND execution_id=? AND span_id=? AND state='queued'",
                                 (self.config.destination, execution, identity))
                self._busy = True
                return observation, list(reversed(chain))
        return None

    def _payload(self, observation, chain):
        # Revalidate the final outbound boundary. Local span dictionaries and
        # workflow/model I/O are never accepted by this exporter.
        observation = MetadataObservation.model_validate(observation.model_dump())
        metadata = observation.metadata.model_dump()
        metadata.update(observation_kind=observation.kind, observation_status=observation.status,
                        content_policy="metadata_only", canonical_usage_only=True)
        # All remote runs are metadata chains, avoiding provider/token billing
        # inference. Canonical nullable usage stays solely in the metadata.
        return {"id": run_id(observation.execution_id, observation.span_id), "name": observation.name,
                "run_type": "chain", "session_id": self.config.project_id,
                "start_time": _time(observation.start_ns).isoformat(), "end_time": _time(observation.end_ns).isoformat(),
                "inputs": {}, "outputs": {}, "extra": {"metadata": metadata},
                "parent_run_id": run_id(observation.execution_id, observation.parent_span_id) if observation.parent_span_id else None,
                "trace_id": run_id(chain[0].execution_id, chain[0].span_id),
                "dotted_order": ".".join(_time(x.start_ns).strftime("%Y%m%dT%H%M%S%fZ") + run_id(x.execution_id, x.span_id) for x in chain)}

    def _work(self):
        try:
            while not self._stop.is_set():
                self._wake.wait(0.25)
                self._wake.clear()
                while not self._stop.is_set():
                    try:
                        job = self._next()
                    except Exception:
                        self.invalid += 1
                        break
                    if job is None:
                        break
                    observation, chain = job
                    self._busy = True
                    state = "failed_or_unknown"
                    try:
                        payload = self._payload(observation, chain)
                        response = self._client.post("/runs", json=payload)
                        if 200 <= response.status_code < 300:
                            state = "exported"
                    except Exception:
                        pass
                    finally:
                        try:
                            with self._lock, self._db:
                                self._db.execute("UPDATE langsmith_exports SET state=? WHERE destination=? AND execution_id=? AND span_id=?",
                                    (state, self.config.destination, observation.execution_id, observation.span_id))
                        except Exception:
                            # The durable claim survives; ambiguity must never
                            # turn into a duplicate request on restart.
                            self.invalid += 1
                        self._busy = False
        finally:
            self._client.close()
            with self._lock:
                try:
                    self._last_states = self._states()
                finally:
                    self._db.close()
                    self._db = None

    def _states(self):
        return dict(self._db.execute("SELECT state,count(*) FROM langsmith_exports WHERE destination=? GROUP BY state",
                                    (self.config.destination,)))

    def summary(self):
        with self._lock:
            states = self._states() if self._db is not None else self._last_states
        return {"enabled": True, "content_policy": "metadata_only", "states": states, "invalid": self.invalid}

    def flush(self, timeout=1.0):
        deadline = time.monotonic() + max(0, min(float(timeout), 5.0))
        self._wake.set()
        while time.monotonic() < deadline:
            with self._lock:
                states = self._states() if self._db is not None else self._last_states
                if not self._busy and not states.get("queued", 0):
                    return True
            time.sleep(0.01)
        return False

    def close(self, timeout=0.5):
        if self._closed:
            return
        self._closed = True
        try:
            self.flush(timeout)
        finally:
            self._stop.set()
            self._wake.set()
            self._thread.join(timeout=0.05)
