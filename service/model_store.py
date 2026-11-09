"""Canonical PostgreSQL model attempts and shared monetary reservations.

Never restore these tables from a workflow checkpoint. Unknown provider outcomes
keep their reservation, and a new authorized budget scope never resets an old one.
"""

from decimal import Decimal
import re
from uuid import uuid4

from psycopg.types.json import Jsonb

from service.errors import ServiceError


def _money(value):
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise ServiceError("MODEL_MONEY_INVALID", 422)
    return value


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,200}", value):
        raise ServiceError("MODEL_IDENTIFIER_INVALID", 422)
    return value


class PostgresModelStore:
    def __init__(self, db):
        self.db = db

    def initialize_budget(self, budget_id, *, limit_usd=Decimal("10"),
                          prior_spend_usd=None, authorization_ref=None):
        """Insert a new scope once. Existing history is immutable through this API."""
        _identifier(budget_id)
        if not Decimal(0) < _money(limit_usd) <= Decimal(10):
            raise ServiceError("MODEL_BUDGET_LIMIT_INVALID", 422)
        if prior_spend_usd is not None:
            _money(prior_spend_usd)
            _identifier(authorization_ref)
        elif authorization_ref is not None:
            _identifier(authorization_ref)
        with self.db.connect() as conn:
            conn.execute("""INSERT INTO model_budget_accounts
                (budget_id,limit_usd,prior_spend_usd,authorization_ref) VALUES (%s,%s,%s,%s)
                ON CONFLICT (budget_id) DO NOTHING""",
                (budget_id, limit_usd, prior_spend_usd, authorization_ref))
            row = conn.execute("SELECT * FROM model_budget_accounts WHERE budget_id=%s FOR UPDATE", (budget_id,)).fetchone()
            if (row["limit_usd"], row["prior_spend_usd"], row["authorization_ref"]) != (limit_usd, prior_spend_usd, authorization_ref):
                raise ServiceError("MODEL_BUDGET_ALREADY_INITIALIZED", 409)
            return dict(row)

    def ensure_unknown_budget(self, budget_id):
        """Convenience for Fake setup only; never overwrite an initialized account."""
        _identifier(budget_id)
        with self.db.connect() as conn:
            conn.execute("""INSERT INTO model_budget_accounts (budget_id,limit_usd)
                VALUES (%s,10) ON CONFLICT (budget_id) DO NOTHING""", (budget_id,))

    @staticmethod
    def _total(conn, *, budget_id=None, execution_id=None):
        if execution_id is not None:
            row = conn.execute("""SELECT COALESCE(sum(COALESCE(cost_usd,0)+held_usd),0) AS amount
                FROM model_attempts WHERE execution_id=%s AND execution_mode='live'""", (execution_id,)).fetchone()
        else:
            row = conn.execute("""SELECT COALESCE(sum(COALESCE(a.cost_usd,0)+a.held_usd),0) AS amount
                FROM model_attempts a JOIN model_executions e USING(execution_id)
                WHERE e.budget_id=%s AND a.execution_mode='live'""", (budget_id,)).fetchone()
        return row["amount"]

    def reserve(self, *, budget_id, execution_id, company_id, actor_id, scope_hash,
                call_id, retry_of, request_hash, mode, provider, model, reserved_usd, metadata):
        _money(reserved_usd)
        if mode not in ("fake", "live") or (mode == "fake" and reserved_usd != 0):
            raise ServiceError("MODEL_MODE_INVALID", 422)
        with self.db.connect() as conn:
            account = conn.execute("SELECT * FROM model_budget_accounts WHERE budget_id=%s FOR UPDATE", (budget_id,)).fetchone()
            if not account:
                raise ServiceError("MODEL_BUDGET_NOT_INITIALIZED", 409)
            conn.execute("""INSERT INTO model_executions (execution_id,budget_id,company_id,actor_id,scope_hash)
                VALUES (%s,%s,%s,%s,%s) ON CONFLICT (execution_id) DO NOTHING""",
                (execution_id, budget_id, company_id, actor_id, scope_hash))
            execution = conn.execute("SELECT * FROM model_executions WHERE execution_id=%s FOR UPDATE", (execution_id,)).fetchone()
            if (execution["budget_id"], execution["company_id"], execution["actor_id"], execution["scope_hash"]) != (budget_id, company_id, actor_id, scope_hash):
                raise ServiceError("MODEL_EXECUTION_SCOPE_MISMATCH", 409)
            if retry_of is None:
                previous = conn.execute("SELECT * FROM model_attempts WHERE execution_id=%s AND call_id=%s AND retry_of IS NULL", (execution_id, call_id)).fetchone()
            else:
                previous = conn.execute("SELECT * FROM model_attempts WHERE retry_of=%s", (retry_of,)).fetchone()
            if previous:
                if (previous["execution_id"], previous["call_id"], previous["request_hash"]) != (execution_id, call_id, request_hash):
                    raise ServiceError("MODEL_IDEMPOTENCY_CONFLICT", 409)
                return {**dict(previous), "new": False}
            if retry_of is not None:
                parent = conn.execute("SELECT * FROM model_attempts WHERE attempt_id=%s", (retry_of,)).fetchone()
                if (not parent or parent["execution_id"] != execution_id or parent["call_id"] != call_id
                        or parent["request_hash"] != request_hash):
                    raise ServiceError("MODEL_RETRY_PARENT_INVALID", 409)
                if parent["status"] != "failed" or parent["held_usd"] != 0:
                    raise ServiceError("MODEL_RETRY_OUTCOME_UNRESOLVED", 409)
                if execution["retry_count"] >= 1:
                    raise ServiceError("MODEL_RETRY_BUDGET_EXHAUSTED", 429)
            if mode == "live":
                if account["prior_spend_usd"] is None:
                    raise ServiceError("MODEL_PRIOR_SPEND_UNKNOWN", 409)
                if account["blocked"]:
                    raise ServiceError("MODEL_BUDGET_RECONCILIATION_REQUIRED", 409)
                if reserved_usd <= 0:
                    raise ServiceError("MODEL_RESERVATION_REQUIRED", 409)
                if self._total(conn, execution_id=execution_id) + reserved_usd > Decimal("0.50"):
                    raise ServiceError("MODEL_QUESTION_BUDGET_EXHAUSTED", 429)
                if account["prior_spend_usd"] + self._total(conn, budget_id=budget_id) + reserved_usd > account["limit_usd"]:
                    raise ServiceError("MODEL_TOTAL_BUDGET_EXHAUSTED", 429)
            attempt_id = uuid4().hex
            row = conn.execute("""INSERT INTO model_attempts
                (attempt_id,execution_id,call_id,retry_of,request_hash,execution_mode,status,
                 requested_provider,requested_model,reserved_usd,held_usd,metadata)
                VALUES (%s,%s,%s,%s,%s,%s,'reserved',%s,%s,%s,%s,%s) RETURNING *""",
                (attempt_id, execution_id, call_id, retry_of, request_hash, mode, provider, model,
                 reserved_usd, reserved_usd, Jsonb(metadata))).fetchone()
            if retry_of is not None:
                conn.execute("UPDATE model_executions SET retry_count=retry_count+1 WHERE execution_id=%s", (execution_id,))
            return {**dict(row), "new": True}

    def mark_dispatched(self, attempt_id):
        with self.db.connect() as conn:
            row = conn.execute("""UPDATE model_attempts SET status='dispatched',dispatched_at=now()
                WHERE attempt_id=%s AND status='reserved' RETURNING attempt_id""", (attempt_id,)).fetchone()
            if not row:
                raise ServiceError("MODEL_ATTEMPT_ALREADY_DISPATCHED", 409)

    def finish(self, attempt_id, *, status, usage, result=None, error_code=None,
               actual_provider=None, actual_model=None, provider_request_id=None, not_dispatched=False):
        if status not in ("completed", "failed", "outcome_unknown"):
            raise ServiceError("MODEL_ATTEMPT_STATUS_INVALID", 422)
        cost = usage.get("cost_usd")
        if cost is not None:
            _money(cost)
        encoded_usage = {**usage, "cost_usd": str(cost) if cost is not None else None}
        with self.db.connect() as conn:
            # Every monetary transition takes the same account lock as reserve.
            ref = conn.execute("""SELECT e.budget_id FROM model_attempts a
                JOIN model_executions e USING(execution_id) WHERE a.attempt_id=%s""", (attempt_id,)).fetchone()
            if not ref:
                raise ServiceError("MODEL_ATTEMPT_UNKNOWN", 409)
            conn.execute("SELECT budget_id FROM model_budget_accounts WHERE budget_id=%s FOR UPDATE", (ref["budget_id"],))
            row = conn.execute("SELECT * FROM model_attempts WHERE attempt_id=%s FOR UPDATE", (attempt_id,)).fetchone()
            if row["status"] in ("completed", "failed", "outcome_unknown"):
                if (row["status"], row["usage"], row["result"], row["error_code"]) != (status, encoded_usage, result, error_code):
                    raise ServiceError("MODEL_ATTEMPT_FINALIZATION_CONFLICT", 409)
                return dict(row)
            if not_dispatched and row["status"] != "reserved":
                raise ServiceError("MODEL_DISPATCH_ALREADY_STARTED", 409)
            if not not_dispatched and row["status"] != "dispatched":
                raise ServiceError("MODEL_DISPATCH_NOT_STARTED", 409)
            held = Decimal(0) if row["execution_mode"] == "fake" or cost is not None or not_dispatched else row["reserved_usd"]
            violation = row["execution_mode"] == "live" and cost is not None and cost > row["reserved_usd"]
            if violation:
                conn.execute("UPDATE model_budget_accounts SET blocked=true WHERE budget_id=%s", (ref["budget_id"],))
                status, result, error_code = "failed", None, "MODEL_COST_EXCEEDED_RESERVATION"
            row = conn.execute("""UPDATE model_attempts SET status=%s,held_usd=%s,cost_usd=%s,usage=%s,
                result=%s,error_code=%s,actual_provider=%s,actual_model=%s,provider_request_id=%s,finished_at=now()
                WHERE attempt_id=%s RETURNING *""", (status, held, cost, Jsonb(encoded_usage), Jsonb(result) if result is not None else None,
                error_code, actual_provider, actual_model, provider_request_id, attempt_id)).fetchone()
            return dict(row)

    def budget_status(self, budget_id):
        with self.db.connect() as conn:
            account = conn.execute("SELECT * FROM model_budget_accounts WHERE budget_id=%s", (budget_id,)).fetchone()
            if not account:
                raise ServiceError("MODEL_BUDGET_NOT_INITIALIZED", 409)
            known = conn.execute("""SELECT COALESCE(sum(a.cost_usd),0) AS cost,
                COALESCE(sum(a.held_usd),0) AS held, count(*) AS attempts
                FROM model_attempts a JOIN model_executions e USING(execution_id)
                WHERE e.budget_id=%s AND a.execution_mode='live'""", (budget_id,)).fetchone()
            return {"budget_id": budget_id, "limit_usd": str(account["limit_usd"]),
                    "prior_spend_usd": str(account["prior_spend_usd"]) if account["prior_spend_usd"] is not None else None,
                    "prior_spend_status": "UNKNOWN" if account["prior_spend_usd"] is None else "KNOWN_WITHIN_SCOPE",
                    "known_cost_usd": str(known["cost"]), "held_usd": str(known["held"]),
                    "live_attempts": known["attempts"], "blocked": account["blocked"]}

    def execution_summary(self, execution_id=None):
        """Metadata-only counters; Host authorizes per-execution access first."""
        clause, params = ("", ()) if execution_id is None else ("WHERE execution_id=%s", (execution_id,))
        with self.db.connect() as conn:
            row = conn.execute("""SELECT
                count(*) FILTER (WHERE execution_mode='live' AND dispatched_at IS NOT NULL) AS live_calls,
                count(*) FILTER (WHERE execution_mode='fake' AND dispatched_at IS NOT NULL) AS fake_calls,
                COALESCE(sum(cost_usd) FILTER (WHERE execution_mode='live'),0) AS known_cost_usd,
                COALESCE(sum(held_usd) FILTER (WHERE execution_mode='live'),0) AS held_usd,
                count(*) FILTER (WHERE execution_mode='live' AND dispatched_at IS NOT NULL
                    AND (cost_usd IS NULL OR usage->>'input_tokens' IS NULL
                         OR usage->>'output_tokens' IS NULL)) AS usage_unknown
                FROM model_attempts """ + clause, params).fetchone()
            return {"live_calls": row["live_calls"], "fake_calls": row["fake_calls"],
                    "known_cost_usd": str(row["known_cost_usd"]), "held_usd": str(row["held_usd"]),
                    "usage_unknown": row["usage_unknown"]}
