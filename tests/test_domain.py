"""PostgreSQL integration tests with independently authored synthetic evidence.

Set TEST_DATABASE_URL to a disposable development PostgreSQL instance. Each test
owns a random schema; no private fixture or operating ledger is read or reset.
"""

from concurrent.futures import ThreadPoolExecutor
import os
import unittest
from uuid import uuid4

import psycopg
from psycopg import sql
from pydantic import ValidationError

from service.contracts import EvidenceDocument
from service.db import Database
from service.domain import DomainService
from service.errors import ServiceError
from service.identity import IdentityService


def content(message="independent synthetic evidence"):
    return EvidenceDocument(payload={"summary": message}, evidence_refs=["test-source:alpha"],
                            source_version="synthetic-1", ontology_version="test-1", rule_version="test-1")


class DocumentContractTests(unittest.TestCase):
    def test_identity_injection_nan_and_extra_contract_fields_are_rejected(self):
        original = content().model_dump()
        for payload in ({"actor_id": "forged"}, {"nested": [{"approved": True}]}, {"value": float("nan")}):
            with self.subTest(payload=repr(payload)), self.assertRaises((ValidationError, ValueError)):
                EvidenceDocument.model_validate({**original, "payload": payload})
        with self.assertRaises(ValidationError):
            EvidenceDocument.model_validate({**original, "role": "admin"})


@unittest.skipUnless(os.environ.get("TEST_DATABASE_URL"), "TEST_DATABASE_URL is required for actual PostgreSQL tests")
class DomainTests(unittest.TestCase):
    def setUp(self):
        self.dsn = os.environ["TEST_DATABASE_URL"]
        self.schema = "domain_test_" + uuid4().hex
        with psycopg.connect(self.dsn) as conn:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.addCleanup(self.remove_schema)
        self.db = Database(self.dsn, schema=self.schema)
        self.db.migrate()
        self.identity = IdentityService(self.db, enabled=True, app_env="test")
        self.identity.provision_dev_accounts()
        self.author = self.identity.login("dev-operator-a").principal
        self.reviewer = self.identity.login("dev-reviewer-b").principal
        self.viewer = self.identity.login("dev-viewer-f").principal
        self.other = self.identity.login("dev-other-g").principal
        self.company = self.author.company_id
        self.domain = DomainService(self.db, self.identity)

    def remove_schema(self):
        with psycopg.connect(self.dsn) as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))

    def assert_error(self, code, action):
        with self.assertRaises(ServiceError) as caught:
            action()
        self.assertEqual(caught.exception.code, code)

    def create(self, actor=None, key="create-one"):
        return self.domain.create(actor or self.author, self.company, content(), key)

    def action(self, method, actor, document, key):
        return getattr(self.domain, method)(actor, self.company, document["id"], document["version"], document["payload_hash"], key)

    def approved(self):
        document = self.create()
        reviewed = self.action("review", self.reviewer, document, "review-one")
        self.assertEqual(reviewed["status"], "REVIEWED")
        return self.action("approve", self.reviewer, reviewed, "approve-one")

    def test_lifecycle_durable_reconciliation_and_exactly_one_ledger_effect(self):
        approved = self.approved()
        self.assertEqual(approved["status"], "APPROVED")
        self.assertIsNone(approved["ledger_version"])
        posted = self.action("post", self.author, approved, "post-one")
        self.assertEqual(posted["status"], "POSTED")
        self.assertEqual(posted["ledger_version"], 1)
        # A new service instance reuses persisted bytes and operation result.
        resumed = DomainService(self.db, IdentityService(self.db, enabled=True, app_env="test"))
        self.assertEqual(resumed.operation(self.author, self.company, posted["operation_id"]), posted)
        self.assertEqual(self.action("post", self.author, approved, "post-one"), posted)
        self.assert_error("EXACT_APPROVAL_REQUIRED", lambda: self.action("post", self.author, approved, "different-post-key"))
        with self.db.connect() as conn:
            for table, expected in (("ledger_events", 1), ("outbox", 1), ("audit_events", 4), ("operations", 4)):
                self.assertEqual(conn.execute(sql.SQL("SELECT count(*) AS n FROM {}").format(sql.Identifier(table))).fetchone()["n"], expected)
            self.assertEqual(conn.execute("SELECT version FROM ledger_state").fetchone()["version"], 1)

    def test_self_approval_review_and_unapproved_post_cannot_change_ledger(self):
        own = self.create(actor=self.reviewer)
        self.assert_error("SELF_REVIEW_FORBIDDEN", lambda: self.action("review", self.reviewer, own, "self-review"))
        reviewed = self.action("review", self.author, own, "other-review")
        self.assert_error("SELF_APPROVAL_FORBIDDEN", lambda: self.action("approve", self.reviewer, reviewed, "self-approve"))
        self.assert_error("EXACT_APPROVAL_REQUIRED", lambda: self.action("post", self.author, reviewed, "early-post"))
        with self.db.connect() as conn:
            self.assertEqual(conn.execute("SELECT count(*) AS n FROM ledger_events").fetchone()["n"], 0)

    def test_stale_hash_revision_invalidation_and_revision_history(self):
        approved = self.approved()
        self.assert_error("DOCUMENT_VERSION_CHANGED", lambda: self.domain.post(
            self.author, self.company, approved["id"], approved["version"], "0" * 64, "wrong-hash"))
        revised = self.domain.revise(self.author, self.company, approved["id"], approved["version"], approved["payload_hash"], content("corrected evidence"), "revise-one")
        self.assertEqual(revised["version"], 2)
        self.assertEqual(revised["status"], "DRAFT")
        self.assertIsNone(revised["approved_hash"])
        self.assert_error("DOCUMENT_VERSION_CHANGED", lambda: self.action("post", self.author, approved, "old-post"))
        with self.db.connect() as conn:
            self.assertEqual(conn.execute("SELECT count(*) AS n FROM document_revisions").fetchone()["n"], 2)

    def test_concurrent_retry_and_changed_payload_collision(self):
        approved = self.approved()
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: self.action("post", self.author, approved, "simultaneous-post"), range(2)))
        self.assertEqual(results[0], results[1])
        self.assert_error("IDEMPOTENCY_CONFLICT", lambda: self.domain.post(
            self.author, self.company, approved["id"], 2, approved["payload_hash"], "simultaneous-post"))
        self.assert_error("IDEMPOTENCY_CONFLICT", lambda: self.domain.create(self.author, self.company, content("changed input"), "create-one"))
        with self.db.connect() as conn:
            self.assertEqual(conn.execute("SELECT count(*) AS n FROM ledger_events").fetchone()["n"], 1)

    def test_required_audit_failure_rolls_back_post_event_outbox_version_and_operation(self):
        approved = self.approved()
        with self.db.connect() as conn:
            conn.execute("""
                CREATE FUNCTION reject_post_audit() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                    IF NEW.action = 'document.post' THEN RAISE EXCEPTION 'injected audit failure'; END IF;
                    RETURN NEW;
                END $$;
                CREATE TRIGGER audit_failure BEFORE INSERT ON audit_events
                FOR EACH ROW EXECUTE FUNCTION reject_post_audit();
            """)
        with self.assertRaises(psycopg.Error):
            self.action("post", self.author, approved, "failed-post")
        current = self.domain.get(self.author, self.company, approved["id"])
        self.assertEqual(current["status"], "APPROVED")
        with self.db.connect() as conn:
            self.assertEqual(conn.execute("SELECT count(*) AS n FROM ledger_events").fetchone()["n"], 0)
            self.assertEqual(conn.execute("SELECT count(*) AS n FROM outbox").fetchone()["n"], 0)
            self.assertEqual(conn.execute("SELECT count(*) AS n FROM ledger_state").fetchone()["n"], 0)
            self.assertEqual(conn.execute("SELECT count(*) AS n FROM operations WHERE idempotency_key='failed-post'").fetchone()["n"], 0)

    def test_permissions_tenant_boundary_and_reconciliation_scope(self):
        document = self.create()
        self.assert_error("PERMISSION_DENIED", lambda: self.domain.create(self.viewer, self.company, content(), "viewer-create"))
        self.assert_error("PERMISSION_DENIED", lambda: self.domain.get(self.other, self.company, document["id"]))
        self.assert_error("DOCUMENT_NOT_FOUND", lambda: self.domain.get(self.other, self.other.company_id, document["id"]))
        self.assert_error("OPERATION_NOT_FOUND", lambda: self.domain.operation(self.reviewer, self.company, document["operation_id"]))

    def test_approver_revocation_and_policy_change_rechecked_at_post(self):
        approved = self.approved()
        with self.db.connect() as conn:
            conn.execute("UPDATE identity_accounts SET active=false WHERE actor_id=%s", (self.reviewer.actor_id,))
        self.assert_error("APPROVER_PERMISSION_REVOKED", lambda: self.action("post", self.author, approved, "revoked-post"))
        with self.db.connect() as conn:
            conn.execute("UPDATE identity_accounts SET active=true,policy_version=policy_version+1 WHERE actor_id=%s", (self.reviewer.actor_id,))
        self.assert_error("APPROVAL_PERMISSION_CHANGED", lambda: self.action("post", self.author, approved, "changed-policy-post"))
        self.assertEqual(self.domain.get(self.author, self.company, approved["id"])["status"], "APPROVED")

    def test_session_revoked_after_request_validation_blocks_domain_transaction(self):
        credentials = self.identity.login("dev-operator-a")
        principal = self.identity.validate_write(credentials.token, credentials.csrf_token, credentials.principal.identity_revision)
        self.identity.logout(credentials.token, credentials.csrf_token, credentials.principal.identity_revision)
        self.assert_error("IDENTITY_REQUIRED", lambda: self.domain.create(principal, self.company, content(), "revoked-session"))


if __name__ == "__main__":
    unittest.main()
