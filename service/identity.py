"""Explicit local development identity with durable, revocable server sessions.

HTTP handlers enforce Host/Origin and browser request headers before calling this
module. Domain services recheck the returned principal inside their transaction;
client-supplied roles, tenant IDs and actor names never confer authority.
"""

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import hmac
import secrets
from uuid import uuid4

from service.errors import ServiceError


READ = ("document.read", "ledger.read", "graph.read", "ontology.read", "query.read")
AUTHOR = READ + ("document.create", "document.update", "document.review", "document.post")
REVIEWER = AUTHOR + ("document.approve",)
# These are synthetic development identities, never operating company accounts.
DEV_ACCOUNTS = (
    ("dev-operator-a", "actor-dev-a", "DEMO-A", "작성자 A", AUTHOR, True),
    ("dev-reviewer-b", "actor-dev-b", "DEMO-A", "검토자 B", REVIEWER, True),
    ("dev-material-c", "actor-dev-c", "DEMO-A", "자재 담당 C", READ, True),
    ("dev-purchase-d", "actor-dev-d", "DEMO-A", "구매 담당 D", READ, True),
    ("dev-warehouse-e", "actor-dev-e", "DEMO-A", "창고 담당 E", READ, True),
    ("dev-viewer-f", "actor-dev-f", "DEMO-A", "조회자 F", READ, True),
    ("dev-other-g", "actor-dev-g", "DEMO-B", "다른 회사 G", READ, True),
    # DEC-H47: another synthetic demand's planner may reserve current stock.
    # New account only; existing accounts' permission profiles stay unchanged.
    ("dev-planner-h", "actor-dev-h", "DEMO-A", "다른 주문 담당 H", READ + ("inventory.reserve",), True),
    ("dev-disabled-x", "actor-dev-x", "DEMO-A", "비활성 계정 X", (), False),
)


def token_hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def csrf_for_token(token: str) -> str:
    # Only the HTTP server knows the HttpOnly cookie. Derivation lets a session
    # read return CSRF without persisting either raw credential in PostgreSQL.
    return hmac.new(token.encode("utf-8"), b"ontologics-csrf-v1", "sha256").hexdigest()


@dataclass(frozen=True)
class Principal:
    actor_id: str
    tenant_id: str
    site_scope: tuple[str, ...]
    permissions: tuple[str, ...]
    permission_profile_version: int
    identity_revision: str
    session_ref: str
    auth_source: str = "dev_switch"

    @property
    def company_id(self):
        return self.tenant_id

    def public(self) -> dict:
        return {
            "actor_id": self.actor_id,
            "tenant_id": self.tenant_id,
            "company_id": self.tenant_id,
            "site_scope": list(self.site_scope),
            "permitted_actions": list(self.permissions),
            "permission_profile_version": self.permission_profile_version,
            "identity_revision": self.identity_revision,
            "auth_source": self.auth_source,
        }


@dataclass(frozen=True)
class SessionCredentials:
    token: str = field(repr=False)
    csrf_token: str = field(repr=False)
    principal: Principal


class IdentityService:
    def __init__(self, db, *, enabled=False, app_env="local", auth_mode="dev_switch", session_ttl_seconds=28800):
        self.db = db
        self.enabled = enabled
        if auth_mode != "dev_switch":
            raise ServiceError("AUTH_PROVIDER_NOT_CONFIGURED", 503)
        if enabled and app_env not in ("local", "test"):
            raise ServiceError("DEV_IDENTITY_FORBIDDEN", 503)
        if not isinstance(session_ttl_seconds, int) or not 1 <= session_ttl_seconds <= 86400:
            raise ServiceError("INVALID_SESSION_TTL", 503)
        self.session_ttl_seconds = session_ttl_seconds

    def _enabled(self):
        if not self.enabled:
            raise ServiceError("DEV_IDENTITY_DISABLED", 404)

    @staticmethod
    def _transaction_lock(conn):
        # This local development implementation serializes identity-sensitive
        # transactions before taking session/account locks. A document post can
        # need caller A and approver B while B switches to A; acquiring these
        # rows in opposite order otherwise deadlocks. Holding this lock through
        # domain commit establishes one order. It is deliberately not a claim of
        # production-scale concurrency; a later policy layer can order scoped
        # locks once every transaction's full actor set is known.
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (674958214,))

    def provision_dev_accounts(self):
        self._enabled()
        with self.db.connect() as conn:
            self._transaction_lock(conn)
            for profile, actor, tenant, label, permissions, active in DEV_ACCOUNTS:
                # Never resurrect disabled accounts or silently reset changed
                # permission profiles on application restart.
                conn.execute("""
                    INSERT INTO identity_accounts
                        (actor_id, dev_profile_id, tenant_id, label, site_scope, permissions, active)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (actor_id) DO NOTHING
                """, (actor, profile, tenant, label, ["MAIN"], list(permissions), active))

    def list_accounts(self):
        self._enabled()
        with self.db.connect() as conn:
            return conn.execute("""
                SELECT dev_profile_id, label, tenant_id, site_scope
                FROM identity_accounts WHERE active ORDER BY dev_profile_id
            """).fetchall()

    def _account(self, conn, profile):
        self._transaction_lock(conn)
        row = conn.execute(
            "SELECT * FROM identity_accounts WHERE dev_profile_id = %s FOR UPDATE", (profile,)
        ).fetchone()
        if row is None:
            raise ServiceError("INVALID_DEV_PROFILE", 422)
        if not row["active"]:
            raise ServiceError("IDENTITY_INACTIVE", 403)
        return row

    def _create_session(self, conn, account, *, event="SESSION_SELECTED", previous_actor=None):
        token = secrets.token_urlsafe(32)
        csrf = csrf_for_token(token)
        ref, revision = uuid4(), uuid4()
        conn.execute("""
            INSERT INTO identity_sessions
                (session_ref, token_hash, csrf_hash, actor_id, identity_revision, policy_version, expires_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (ref, token_hash(token), token_hash(csrf), account["actor_id"], revision,
              account["policy_version"], datetime.now(timezone.utc) + timedelta(seconds=self.session_ttl_seconds)))
        principal = self._principal({**account, "session_ref": ref, "identity_revision": revision})
        self._audit(conn, event, principal, previous_actor)
        return SessionCredentials(token, csrf, principal)

    @staticmethod
    def _principal(row):
        return Principal(row["actor_id"], row["tenant_id"], tuple(row["site_scope"]),
                         tuple(row["permissions"]), row["policy_version"],
                         str(row["identity_revision"]), str(row["session_ref"]))

    @staticmethod
    def _audit(conn, event, principal, previous_actor=None):
        conn.execute("""
            INSERT INTO identity_audit
                (audit_id, event_type, actor_id, previous_actor_id, tenant_id,
                 session_ref, identity_revision, policy_version, auth_source)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (uuid4(), event, principal.actor_id, previous_actor, principal.tenant_id,
              principal.session_ref, principal.identity_revision,
              principal.permission_profile_version, principal.auth_source))

    def login(self, dev_profile_id, *, current_token=None):
        self._enabled()
        with self.db.connect() as conn:
            self._transaction_lock(conn)
            if current_token:
                existing = conn.execute("""
                    SELECT session_ref FROM identity_sessions
                    WHERE token_hash = %s AND revoked_at IS NULL AND expires_at > now()
                    FOR UPDATE
                """, (token_hash(current_token),)).fetchone()
                if existing:
                    raise ServiceError("IDENTITY_ALREADY_SELECTED", 409)
            return self._create_session(conn, self._account(conn, dev_profile_id))

    def _session(self, conn, *, token=None, session_ref=None, lock=False):
        if not token and not session_ref:
            raise ServiceError("IDENTITY_REQUIRED", 401)
        if lock:
            self._transaction_lock(conn)
        where, value = ("s.token_hash = %s", token_hash(token)) if token else ("s.session_ref = %s", session_ref)
        row = conn.execute(f"""
            SELECT a.*, s.session_ref, s.identity_revision, s.csrf_hash,
                   s.policy_version AS session_policy_version, s.expires_at,
                   s.revoked_at, s.write_in_progress
            FROM identity_sessions s JOIN identity_accounts a ON a.actor_id = s.actor_id
            WHERE {where}
            {"FOR UPDATE OF s, a" if lock else ""}
        """, (value,)).fetchone()
        if row is None or row["revoked_at"] is not None or row["expires_at"] <= datetime.now(timezone.utc):
            raise ServiceError("IDENTITY_REQUIRED", 401)
        if not row["active"]:
            raise ServiceError("IDENTITY_INACTIVE", 403)
        if row["session_policy_version"] != row["policy_version"]:
            raise ServiceError("IDENTITY_CHANGED", 409)
        return row

    def authenticate(self, token):
        self._enabled()
        with self.db.connect() as conn:
            return self._principal(self._session(conn, token=token))

    @staticmethod
    def _validate_write(row, csrf_token, expected_revision):
        if not csrf_token or not hmac.compare_digest(token_hash(csrf_token), row["csrf_hash"]):
            raise ServiceError("CSRF_REJECTED", 403)
        if not expected_revision or str(row["identity_revision"]) != str(expected_revision):
            raise ServiceError("IDENTITY_CHANGED", 409)

    def validate_write(self, token, csrf_token, expected_revision):
        self._enabled()
        with self.db.connect() as conn:
            row = self._session(conn, token=token)
            self._validate_write(row, csrf_token, expected_revision)
            return self._principal(row)

    @contextmanager
    def write_context(self, token, csrf_token, expected_revision):
        self._enabled()
        with self.db.connect() as conn:
            row = self._session(conn, token=token, lock=True)
            self._validate_write(row, csrf_token, expected_revision)
            yield conn, self._principal(row)

    def require(self, principal, action, connection, company_id=None):
        self._enabled()
        row = self._session(connection, session_ref=principal.session_ref, lock=True)
        fresh = self._principal(row)
        if fresh != principal:
            raise ServiceError("IDENTITY_CHANGED", 409)
        if fresh.tenant_id != (company_id or principal.tenant_id) or action not in fresh.permissions:
            raise ServiceError("PERMISSION_DENIED", 403)
        return fresh

    def authorize(self, principal, tenant_id, action, *, connection=None, lock=False):
        # require() always locks for callers holding a write transaction.
        if connection is not None:
            return self.require(principal, action, connection, tenant_id)
        with self.db.connect() as conn:
            return self.require(principal, action, conn, tenant_id)

    def require_actor(self, actor_id, company_id, action, connection):
        self._enabled()
        self._transaction_lock(connection)
        row = connection.execute(
            "SELECT * FROM identity_accounts WHERE actor_id = %s FOR UPDATE", (actor_id,)
        ).fetchone()
        if not row or not row["active"] or row["tenant_id"] != company_id or action not in row["permissions"]:
            raise ServiceError("APPROVER_PERMISSION_REVOKED", 403)
        return {"actor_id": row["actor_id"], "company_id": row["tenant_id"],
                "permission_profile_version": row["policy_version"]}

    def switch(self, token, dev_profile_id, csrf_token, expected_revision):
        self._enabled()
        with self.db.connect() as conn:
            row = self._session(conn, token=token, lock=True)
            self._validate_write(row, csrf_token, expected_revision)
            if row["write_in_progress"]:
                raise ServiceError("SESSION_SWITCH_BLOCKED", 409)
            account = self._account(conn, dev_profile_id)
            conn.execute("UPDATE identity_sessions SET revoked_at = now() WHERE session_ref = %s", (row["session_ref"],))
            return self._create_session(conn, account, event="SESSION_SWITCHED", previous_actor=row["actor_id"])

    def logout(self, token, csrf_token, expected_revision):
        self._enabled()
        with self.db.connect() as conn:
            row = self._session(conn, token=token, lock=True)
            self._validate_write(row, csrf_token, expected_revision)
            if row["write_in_progress"]:
                raise ServiceError("SESSION_SWITCH_BLOCKED", 409)
            conn.execute("UPDATE identity_sessions SET revoked_at = now() WHERE session_ref = %s", (row["session_ref"],))
            self._audit(conn, "SESSION_ENDED", self._principal(row))
