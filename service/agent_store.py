"""Durable workflow control, counters and artifacts outside LangGraph state.

The session advisory lock prevents concurrent workers; the persisted lease token
fences writes from an obsolete worker. Neither mechanism authorizes ledger writes.
"""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from service.agent_contracts import CHECKPOINT_VERSION, WORKFLOW_VERSION
from service.contracts import content_hash
from service.errors import ServiceError


TERMINAL = frozenset({"COMPLETED", "FAILED", "CANCELLED"})


def identifier(value):
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise ServiceError("AGENT_EXECUTION_NOT_FOUND", 404) from None


class AgentStore:
    def __init__(self, db):
        self.db = db

    def create(self, principal, request, context, *, max_tool_calls, deadline_seconds, runtime_versions):
        body = request.model_dump(mode="json")
        digest = content_hash(body)
        with self.db.connect() as conn:
            # Serialize deduplication without changing any business-domain table.
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (674958217,))
            previous = conn.execute("""SELECT * FROM agent_executions
                WHERE company_id=%s AND owner_actor_id=%s AND idempotency_key=%s""",
                (principal.company_id, principal.actor_id, request.idempotency_key)).fetchone()
            if previous:
                if previous["request_hash"] != digest:
                    raise ServiceError("AGENT_IDEMPOTENCY_CONFLICT", 409)
                return str(previous["execution_id"])
            execution_id = context.execution_id
            conn.execute("""INSERT INTO agent_executions
                (execution_id,thread_id,company_id,owner_actor_id,idempotency_key,request_hash,
                 request,pinned_context,policy_version,workflow_version,checkpoint_version,
                 max_tool_calls,deadline_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (execution_id, uuid4(), principal.company_id, principal.actor_id,
                 request.idempotency_key, digest, Jsonb({**body, "_runtime_versions": runtime_versions}), Jsonb(context.model_dump(mode="json")),
                 principal.permission_profile_version, WORKFLOW_VERSION, CHECKPOINT_VERSION,
                 max_tool_calls, datetime.now(timezone.utc) + timedelta(seconds=deadline_seconds)))
            self._event(conn, execution_id, None, "created", "CREATED")
            return execution_id

    def get(self, execution_id):
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM agent_executions WHERE execution_id=%s",
                               (identifier(execution_id),)).fetchone()
        if row is None:
            raise ServiceError("AGENT_EXECUTION_NOT_FOUND", 404)
        return row

    @staticmethod
    def _event(conn, execution_id, segment_id, stage, status, metadata=None):
        conn.execute("""INSERT INTO agent_events(execution_id,segment_id,stage,status,metadata)
            VALUES (%s,%s,%s,%s,%s)""",
            (execution_id, segment_id, stage, status, Jsonb(metadata or {})))

    def event(self, execution_id, lease, segment_id, stage, status, metadata=None):
        with self.db.connect() as conn:
            self._fenced(conn, execution_id, lease)
            self._event(conn, execution_id, segment_id, stage, status, metadata)

    @staticmethod
    def _fenced(conn, execution_id, lease):
        row = conn.execute("SELECT * FROM agent_executions WHERE execution_id=%s FOR UPDATE",
                           (execution_id,)).fetchone()
        if row is None or str(row["lease_token"]) != str(lease):
            raise ServiceError("AGENT_LEASE_LOST", 409)
        return row

    @contextmanager
    def claim(self, execution_id, *, expected_revision=None):
        execution_id = identifier(execution_id)
        key = int.from_bytes(sha256(execution_id.encode()).digest()[:8], "big", signed=True)
        with self.db.connect() as guard:
            guard.autocommit = True
            locked = guard.execute("SELECT pg_try_advisory_lock(%s) AS acquired", (key,)).fetchone()["acquired"]
            if not locked:
                raise ServiceError("AGENT_EXECUTION_BUSY", 409)
            try:
                with self.db.connect() as conn:
                    row = conn.execute("SELECT * FROM agent_executions WHERE execution_id=%s FOR UPDATE",
                                       (execution_id,)).fetchone()
                    if expected_revision is not None and row["revision"] != expected_revision:
                        raise ServiceError("AGENT_REVISION_CHANGED", 409)
                    if row["status"] in TERMINAL or row["status"] == "WAITING":
                        claimed = None
                    else:
                        lease, segment_id = uuid4(), uuid4()
                        claimed = conn.execute("""UPDATE agent_executions SET status='RUNNING',
                            lease_token=%s,lease_until=now()+interval '60 seconds',
                            segment_count=segment_count+1,revision=revision+1,updated_at=now()
                            WHERE execution_id=%s RETURNING *""", (lease, execution_id)).fetchone()
                        claimed["segment_id"] = str(segment_id)
                        self._event(conn, execution_id, segment_id, "segment", "RUNNING",
                                    {"segment": claimed["segment_count"]})
                yield claimed
            finally:
                guard.execute("SELECT pg_advisory_unlock(%s)", (key,))

    @contextmanager
    def summary_lock(self, execution_id):
        """Same per-execution advisory lock as claim(); yields False when busy."""
        execution_id = identifier(execution_id)
        key = int.from_bytes(sha256(execution_id.encode()).digest()[:8], "big", signed=True)
        with self.db.connect() as guard:
            guard.autocommit = True
            locked = guard.execute("SELECT pg_try_advisory_lock(%s) AS acquired", (key,)).fetchone()["acquired"]
            try:
                yield bool(locked)
            finally:
                if locked:
                    guard.execute("SELECT pg_advisory_unlock(%s)", (key,))

    def put_summary(self, execution_id, key, content):
        """Stores the after-answer summary of a COMPLETED execution once; the answer artifact is untouched."""
        digest = content_hash(content)
        with self.db.connect() as conn:
            row = conn.execute("SELECT status FROM agent_executions WHERE execution_id=%s FOR UPDATE", (execution_id,)).fetchone()
            if row is None or row["status"] != "COMPLETED":
                raise ServiceError("AGENT_SUMMARY_NOT_ALLOWED", 409)
            previous = conn.execute("SELECT * FROM agent_artifacts WHERE execution_id=%s AND artifact_key=%s",
                                    (execution_id, key)).fetchone()
            if previous:
                return previous["content"]
            conn.execute("""INSERT INTO agent_artifacts(artifact_id,execution_id,artifact_key,content,content_hash)
                VALUES (%s,%s,%s,%s,%s)""", (str(uuid4()), execution_id, key, Jsonb(content), digest))
            return content

    def resume(self, execution_id, request):
        digest = content_hash(request.model_dump(mode="json"))
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM agent_executions WHERE execution_id=%s FOR UPDATE",
                               (execution_id,)).fetchone()
            prior = conn.execute("""SELECT request_hash FROM agent_resume_requests
                WHERE execution_id=%s AND idempotency_key=%s""",
                (execution_id, request.idempotency_key)).fetchone()
            if prior:
                if prior["request_hash"] != digest:
                    raise ServiceError("AGENT_IDEMPOTENCY_CONFLICT", 409)
                return False
            if row["revision"] != request.expected_revision:
                raise ServiceError("AGENT_REVISION_CHANGED", 409)
            if row["status"] != "WAITING":
                raise ServiceError("AGENT_NOT_WAITING", 409)
            conn.execute("""INSERT INTO agent_resume_requests(execution_id,idempotency_key,request_hash)
                VALUES (%s,%s,%s)""", (execution_id, request.idempotency_key, digest))
            pending = {"capability": request.capability, "question": request.question}
            if request.product is not None:
                pending["product"] = request.product.model_dump(mode="json")
            conn.execute("""UPDATE agent_executions SET pending_resume=%s,status='CREATED',
                revision=revision+1,updated_at=now() WHERE execution_id=%s""",
                (Jsonb(pending), execution_id))
            self._event(conn, execution_id, None, "resume_requested", "CREATED")
            return True

    def check_budget(self, execution_id, lease, *, reserve=False):
        with self.db.connect() as conn:
            row = self._fenced(conn, execution_id, lease)
            if row["cancel_requested"]:
                raise ServiceError("AGENT_CANCELLED", 409)
            if row["deadline_at"] <= datetime.now(timezone.utc):
                raise ServiceError("AGENT_DEADLINE_EXCEEDED", 408)
            if reserve:
                if row["tool_calls"] >= row["max_tool_calls"]:
                    raise ServiceError("TOOL_BUDGET_EXHAUSTED", 429)
                conn.execute("UPDATE agent_executions SET tool_calls=tool_calls+1 WHERE execution_id=%s", (execution_id,))
            conn.execute("UPDATE agent_executions SET lease_until=now()+interval '60 seconds' WHERE execution_id=%s", (execution_id,))

    def artifact(self, execution_id, key):
        with self.db.connect() as conn:
            return conn.execute("SELECT * FROM agent_artifacts WHERE execution_id=%s AND artifact_key=%s",
                                (execution_id, key)).fetchone()

    def put_artifact(self, execution_id, lease, key, content):
        digest = content_hash(content)
        with self.db.connect() as conn:
            self._fenced(conn, execution_id, lease)
            previous = conn.execute("SELECT * FROM agent_artifacts WHERE execution_id=%s AND artifact_key=%s",
                                    (execution_id, key)).fetchone()
            if previous:
                if previous["content_hash"] != digest:
                    raise ServiceError("AGENT_ARTIFACT_CONFLICT", 409)
                return str(previous["artifact_id"])
            artifact_id = str(uuid4())
            conn.execute("""INSERT INTO agent_artifacts(artifact_id,execution_id,artifact_key,content,content_hash)
                VALUES (%s,%s,%s,%s,%s)""", (artifact_id, execution_id, key, Jsonb(content), digest))
            return artifact_id

    def result(self, execution_id, artifact_id):
        if not artifact_id:
            return None
        with self.db.connect() as conn:
            row = conn.execute("SELECT content,content_hash FROM agent_artifacts WHERE execution_id=%s AND artifact_id=%s",
                               (execution_id, artifact_id)).fetchone()
        if row is None or content_hash(row["content"]) != row["content_hash"]:
            raise ServiceError("AGENT_ARTIFACT_INVALID", 409)
        return row["content"]

    def finish(self, execution_id, lease, segment_id, status, *, result_ref=None, capability=None, interrupt=None, error_code=None):
        with self.db.connect() as conn:
            row = self._fenced(conn, execution_id, lease)
            if row["cancel_requested"]:
                status, error_code = "CANCELLED", "AGENT_CANCELLED"
            conn.execute("""UPDATE agent_executions SET status=%s,result_ref=%s,capability=%s,
                interrupt=%s,error_code=%s,pending_resume=NULL,lease_token=NULL,lease_until=NULL,
                revision=revision+1,updated_at=now() WHERE execution_id=%s""",
                (status, result_ref, capability, Jsonb(interrupt) if interrupt else None, error_code, execution_id))
            self._event(conn, execution_id, segment_id, "segment", status,
                        {"error_code": error_code} if error_code else {})

    def cancel(self, execution_id, expected_revision):
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM agent_executions WHERE execution_id=%s FOR UPDATE", (execution_id,)).fetchone()
            if row["revision"] != expected_revision:
                raise ServiceError("AGENT_REVISION_CHANGED", 409)
            if row["status"] in TERMINAL:
                return
            conn.execute("""UPDATE agent_executions SET cancel_requested=true,
                status=CASE WHEN status='RUNNING' THEN status ELSE 'CANCELLED' END,
                revision=revision+1,updated_at=now() WHERE execution_id=%s""", (execution_id,))
            self._event(conn, execution_id, None, "cancel_requested", "CANCELLED")

    def events(self, execution_id, after=0):
        if type(after) is not int or after < 0:
            raise ServiceError("INVALID_CURSOR", 422)
        with self.db.connect() as conn:
            rows = conn.execute("""SELECT sequence,segment_id,stage,status,metadata,recorded_at
                FROM agent_events WHERE execution_id=%s AND sequence>%s ORDER BY sequence LIMIT 200""",
                (execution_id, after)).fetchall()
        return [{**row, "segment_id": str(row["segment_id"]) if row["segment_id"] else None,
                 "recorded_at": row["recorded_at"].isoformat()} for row in rows]


class DurableAgentBudget:
    def __init__(self, store, execution_id, lease, revalidate):
        self.store, self.execution_id, self.lease, self.revalidate = store, execution_id, lease, revalidate

    @property
    def tool_calls(self):
        return self.store.get(self.execution_id)["tool_calls"]

    @property
    def deadline_at(self):
        return self.store.get(self.execution_id)["deadline_at"]

    def check(self):
        self.revalidate()
        self.store.check_budget(self.execution_id, self.lease)

    def reserve(self, execution_id):
        if execution_id != self.execution_id:
            raise ServiceError("EXECUTION_BUDGET_MISMATCH", 409)
        self.revalidate()
        self.store.check_budget(self.execution_id, self.lease, reserve=True)
