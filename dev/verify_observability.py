#!/usr/bin/env python3
"""Verify metadata-only parent/child ingestion against a local Langfuse instance.

Inject credentials through the environment. Neither keys nor raw API bodies are
printed. This performs no model call and sends synthetic metadata only.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import time
import uuid

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from service.observability import (LangfuseConfig, LangfuseExporter,
                                   MetadataObserver, otel_span_id, trace_id)


def private_file(value):
    path = Path(value).expanduser().resolve()
    repository = Path(__file__).resolve().parents[1]
    if path == repository or repository in path.parents:
        raise ValueError("EVIDENCE_MUST_STAY_OUTSIDE_REPOSITORY")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def read_trace(client, identity, since, until, *, timeout_seconds=60):
    """Use v2 on v4, v1 on existing v3, with bounded queries and polling."""
    endpoint = "/api/public/v2/observations"
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        response = client.get(endpoint, params={
            "traceId": identity, "fromStartTime": since.isoformat(),
            "toStartTime": until.isoformat(), "limit": 100,
        })
        if response.status_code == 404 and endpoint.endswith("v2/observations"):
            endpoint = "/api/public/observations"
            continue
        if response.status_code != 200:
            raise ValueError("OBSERVABILITY_READBACK_FAILED")
        data = response.json().get("data", [])
        if len(data) >= 3:
            return data, endpoint
        time.sleep(1)
    raise ValueError("OBSERVABILITY_READBACK_TIMEOUT")


def verify(receipt_path, *, timeout_seconds=60):
    config = LangfuseConfig.from_env()
    if config is None:
        raise ValueError("OBSERVABILITY_NOT_CONFIGURED")
    now = datetime.now(timezone.utc)
    execution, root, tool, attempt = (uuid.uuid4().hex for _ in range(4))
    observer = MetadataObserver(LangfuseExporter(config), receipt_path=receipt_path)
    metadata = {"execution_mode": "fake", "workflow_version": "observer-canary-v1",
                "ledger_version": 0, "projection_version": 0,
                "prompt": "UNEXPORTED_SYNTHETIC_SENTINEL", "api_key": "UNEXPORTED_SYNTHETIC_SECRET"}
    try:
        for identity, parent, name, kind, additional in [
            (root, None, "workflow.verify", "chain", {}),
            (tool, root, "tool.verify", "tool", {}),
            (attempt, tool, "model.route", "generation",
             {"attempt_id": attempt, "provider": "fake", "model": "fake-synthetic",
              "usage_source": "synthetic", "cost_usd": "0", "input_tokens": 2, "output_tokens": 3}),
        ]:
            if not observer.emit_span(execution, identity, parent, name, kind, "completed",
                                      {**metadata, **additional}, now, now + timedelta(milliseconds=5)):
                raise ValueError("OBSERVABILITY_CANARY_EXPORT_FAILED")
        # Identical logical attempts must not be exported or counted twice.
        if observer.emit_span(execution, attempt, tool, "model.route", "generation", "completed",
                              {**metadata, "attempt_id": attempt}, now, now + timedelta(milliseconds=5)):
            raise ValueError("OBSERVABILITY_DUPLICATE_EXPORT")
        with httpx.Client(base_url=config.base_url, auth=(config.public_key, config.secret_key),
                          timeout=5, trust_env=False, follow_redirects=False) as client:
            health = client.get("/api/public/health")
            if health.status_code != 200:
                raise ValueError("OBSERVABILITY_HEALTH_FAILED")
            rows, endpoint = read_trace(client, trace_id(execution), now - timedelta(minutes=1),
                                       now + timedelta(minutes=5), timeout_seconds=timeout_seconds)
            version = health.json().get("version")
        expected = {otel_span_id(execution, root): None,
                    otel_span_id(execution, tool): otel_span_id(execution, root),
                    otel_span_id(execution, attempt): otel_span_id(execution, tool)}
        if len(rows) != 3 or {row.get("id") for row in rows} != set(expected):
            raise ValueError("OBSERVABILITY_OBSERVATION_COUNT_MISMATCH")
        if any(row.get("parentObservationId") != expected[row["id"]] for row in rows):
            raise ValueError("OBSERVABILITY_PARENT_CHILD_MISMATCH")
        if any(row.get("input") not in (None, "", {}) or row.get("output") not in (None, "", {}) for row in rows):
            raise ValueError("OBSERVABILITY_UNEXPECTED_CONTENT")
        if "UNEXPORTED_SYNTHETIC" in json.dumps(rows):
            raise ValueError("OBSERVABILITY_REDACTION_FAILED")
        return {"status": "VERIFIED", "langfuse_version": version,
                "read_endpoint": endpoint, "trace_id": trace_id(execution),
                "execution_id": execution, "observations": 3,
                "parent_child_verified": True, "raw_io_absent": True,
                "duplicate_exports_prevented": observer.duplicates,
                "real_model_calls": 0, "external_exports": 0,
                "langsmith_enabled": False,
                "observations_summary": [{"id": row["id"],
                    "parentObservationId": row.get("parentObservationId"), "type": row.get("type")}
                    for row in rows]}
    finally:
        observer.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt-file", required=True)
    parser.add_argument("--evidence-file", required=True)
    parser.add_argument("--timeout", type=int, default=60)
    args = parser.parse_args()
    try:
        if not 1 <= args.timeout <= 120:
            raise ValueError("OBSERVABILITY_TIMEOUT_INVALID")
        result = verify(private_file(args.receipt_file), timeout_seconds=args.timeout)
        path = private_file(args.evidence_file)
        path.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
        os.chmod(path, 0o600)
        print(json.dumps({key: result[key] for key in ["status", "langfuse_version",
              "observations", "parent_child_verified", "raw_io_absent", "real_model_calls"]}))
        return 0
    except Exception as error:
        # Even unexpected HTTP/config exceptions may contain sensitive values.
        code = str(error) if isinstance(error, ValueError) and str(error).startswith("OBSERVABILITY_") else "OBSERVABILITY_VERIFICATION_FAILED"
        print(json.dumps({"status": "FAILED", "code": code}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
