"""PostgreSQL identity integration, with independent synthetic development users."""

from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import replace
import os
from pathlib import Path
import tempfile
from threading import Event
import unittest
from unittest.mock import patch
from uuid import uuid4

from psycopg import sql

from service.db import Database
from service.contracts import EvidenceDocument
from service.domain import DomainService
from service.errors import ServiceError
from service.identity import IdentityService, csrf_for_token


@unittest.skipUnless(os.environ.get("TEST_DATABASE_URL"), "TEST_DATABASE_URL not configured")
class IdentityIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dsn = os.environ["TEST_DATABASE_URL"]
        cls.base_db = Database(cls.dsn)
        cls.schema = "test_identity_" + uuid4().hex
        with cls.base_db.connect() as conn:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(cls.schema)))
        cls.db = Database(cls.dsn, schema=cls.schema)
        cls.db.migrate()

    @classmethod
    def tearDownClass(cls):
        with cls.base_db.connect() as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(cls.schema)))

    def setUp(self):
        with self.db.connect() as conn:
            conn.execute("TRUNCATE identity_audit, identity_sessions, identity_accounts CASCADE")
        self.identity = IdentityService(self.db, enabled=True, app_env="test")
        self.identity.provision_dev_accounts()

    def assertCode(self, code, fn, *args, **kwargs):
        with self.assertRaises(ServiceError) as context:
            fn(*args, **kwargs)
        self.assertEqual(context.exception.code, code)
        return context.exception

    def login(self, profile="dev-operator-a"):
        return self.identity.login(profile)

    def test_explicit_local_mode_and_unknown_provider_fail_closed(self):
        for environment in ("production", "staging", "public"):
            self.assertCode("DEV_IDENTITY_FORBIDDEN", IdentityService, self.db,
                            enabled=True, app_env=environment)
        for mode in ("oidc", "unknown"):
            self.assertCode("AUTH_PROVIDER_NOT_CONFIGURED", IdentityService, self.db, auth_mode=mode)
        disabled = IdentityService(self.db)
        self.assertCode("DEV_IDENTITY_DISABLED", disabled.provision_dev_accounts)
        self.assertCode("DEV_IDENTITY_DISABLED", disabled.authenticate, "ignored")

    def test_unselected_unknown_and_disabled_identity_denied(self):
        self.assertCode("IDENTITY_REQUIRED", self.identity.authenticate, None)
        self.assertCode("IDENTITY_REQUIRED", self.identity.authenticate, "forged")
        self.assertCode("INVALID_DEV_PROFILE", self.identity.login, "forged-admin")
        self.assertCode("IDENTITY_INACTIVE", self.identity.login, "dev-disabled-x")
        self.assertNotIn("dev-disabled-x", [a["dev_profile_id"] for a in self.identity.list_accounts()])

    def test_durable_sessions_store_only_hashes_and_safe_audit(self):
        login = self.login()
        self.assertEqual(login.csrf_token, csrf_for_token(login.token))
        self.assertNotIn(login.token, repr(login))
        self.assertNotIn(login.csrf_token, repr(login))
        restarted_service = IdentityService(Database(self.dsn, schema=self.schema), enabled=True, app_env="test")
        self.assertEqual(restarted_service.authenticate(login.token), login.principal)
        with self.db.connect() as conn:
            stored = str(conn.execute("SELECT * FROM identity_sessions").fetchall())
            audit = str(conn.execute("SELECT * FROM identity_audit").fetchall())
        for credential in (login.token, login.csrf_token):
            self.assertNotIn(credential, stored)
            self.assertNotIn(credential, audit)
        self.assertIn(login.principal.actor_id, audit)

    def test_permissions_company_scope_and_forged_principal_denied(self):
        author = self.login().principal
        other = self.login("dev-other-g").principal
        viewer = self.login("dev-viewer-f").principal
        with self.db.connect() as conn:
            self.identity.require(author, "document.create", conn)
            self.assertCode("PERMISSION_DENIED", self.identity.require, author, "document.approve", conn)
            self.assertCode("PERMISSION_DENIED", self.identity.require, other, "document.read", conn, "DEMO-A")
            self.assertCode("PERMISSION_DENIED", self.identity.require, viewer, "document.create", conn)
            forged = replace(author, permissions=author.permissions + ("document.approve",))
            self.assertCode("IDENTITY_CHANGED", self.identity.require, forged, "document.approve", conn)

    def test_csrf_and_stale_revision_rejected_before_mutation(self):
        login = self.login()
        self.assertCode("CSRF_REJECTED", self.identity.validate_write,
                        login.token, "wrong-csrf", login.principal.identity_revision)
        self.assertCode("IDENTITY_CHANGED", self.identity.validate_write,
                        login.token, login.csrf_token, "stale")
        self.assertEqual(self.identity.validate_write(login.token, login.csrf_token,
                         login.principal.identity_revision), login.principal)

    def test_switch_rotates_session_and_keeps_independent_browser_session(self):
        one, independent = self.login(), self.login()
        self.assertCode("IDENTITY_ALREADY_SELECTED", self.identity.login, "dev-reviewer-b", current_token=one.token)
        two = self.identity.switch(one.token, "dev-reviewer-b", one.csrf_token, one.principal.identity_revision)
        self.assertNotEqual(one.token, two.token)
        self.assertNotEqual(one.csrf_token, two.csrf_token)
        self.assertNotEqual(one.principal.identity_revision, two.principal.identity_revision)
        self.assertCode("IDENTITY_REQUIRED", self.identity.authenticate, one.token)
        self.assertEqual(self.identity.authenticate(independent.token), independent.principal)
        with self.db.connect() as conn:
            event = conn.execute("SELECT * FROM identity_audit WHERE event_type = 'SESSION_SWITCHED'").fetchone()
        self.assertEqual(event["previous_actor_id"], one.principal.actor_id)
        self.assertEqual(event["actor_id"], two.principal.actor_id)

    def test_switch_same_actor_still_rotates_and_inactive_target_is_atomic(self):
        one = self.login()
        self.assertCode("IDENTITY_INACTIVE", self.identity.switch, one.token,
                        "dev-disabled-x", one.csrf_token, one.principal.identity_revision)
        self.assertEqual(self.identity.authenticate(one.token), one.principal)
        two = self.identity.switch(one.token, "dev-operator-a", one.csrf_token, one.principal.identity_revision)
        self.assertEqual(one.principal.actor_id, two.principal.actor_id)
        self.assertNotEqual(one.principal.identity_revision, two.principal.identity_revision)

    def test_expiry_logout_and_revocation_are_rechecked(self):
        expired = self.login()
        with self.db.connect() as conn:
            conn.execute("UPDATE identity_sessions SET expires_at = now() - interval '1 second' WHERE session_ref = %s",
                         (expired.principal.session_ref,))
        self.assertCode("IDENTITY_REQUIRED", self.identity.authenticate, expired.token)
        ended = self.login()
        self.identity.logout(ended.token, ended.csrf_token, ended.principal.identity_revision)
        self.assertCode("IDENTITY_REQUIRED", self.identity.authenticate, ended.token)
        revoked = self.login()
        with self.db.connect() as conn:
            conn.execute("UPDATE identity_accounts SET active = false WHERE actor_id = %s", (revoked.principal.actor_id,))
        self.assertCode("IDENTITY_INACTIVE", self.identity.authenticate, revoked.token)
        self.identity.provision_dev_accounts()
        self.assertCode("IDENTITY_INACTIVE", self.identity.authenticate, revoked.token)

    def test_policy_change_invalidates_snapshot_and_approver_authority_rechecked(self):
        login = self.login("dev-reviewer-b")
        with self.db.connect() as conn:
            policy = self.identity.require_actor(login.principal.actor_id, "DEMO-A", "document.approve", conn)
            self.assertEqual(policy["permission_profile_version"], 1)
            conn.execute("UPDATE identity_accounts SET policy_version = 2, permissions = %s WHERE actor_id = %s",
                         (["document.read"], login.principal.actor_id))
        self.assertCode("IDENTITY_CHANGED", self.identity.authenticate, login.token)
        with self.db.connect() as conn:
            self.assertCode("APPROVER_PERMISSION_REVOKED", self.identity.require_actor,
                            login.principal.actor_id, "DEMO-A", "document.approve", conn)

    def test_write_lock_orders_switch_after_confirmed_commit(self):
        login = self.login()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with self.identity.write_context(login.token, login.csrf_token, login.principal.identity_revision) as (conn, principal):
                self.assertEqual(principal, login.principal)
                future = pool.submit(self.identity.switch, login.token, "dev-reviewer-b", login.csrf_token,
                                     login.principal.identity_revision)
                with self.assertRaises(TimeoutError):
                    future.result(timeout=0.1)
            switched = future.result(timeout=3)
        self.assertEqual(switched.principal.actor_id, "actor-dev-b")
        self.assertCode("IDENTITY_REQUIRED", self.identity.authenticate, login.token)

    def test_unknown_write_outcome_blocks_switch_and_logout(self):
        login = self.login()
        with self.db.connect() as conn:
            conn.execute("UPDATE identity_sessions SET write_in_progress = true WHERE session_ref = %s",
                         (login.principal.session_ref,))
        self.assertCode("SESSION_SWITCH_BLOCKED", self.identity.switch, login.token, "dev-reviewer-b",
                        login.csrf_token, login.principal.identity_revision)
        self.assertCode("SESSION_SWITCH_BLOCKED", self.identity.logout, login.token,
                        login.csrf_token, login.principal.identity_revision)

    def test_author_post_and_reviewer_switch_serialize_without_account_deadlock(self):
        """Reproduce the A→B / B→A account lock order with actual domain post."""
        author = self.login()
        reviewer = self.login("dev-reviewer-b")
        domain = DomainService(self.db, self.identity)
        content = EvidenceDocument(payload={"note": "independent concurrency fixture"},
                                   evidence_refs=["urn:test:identity-concurrency"], source_version="v1",
                                   ontology_version="v1", rule_version="v1")
        draft = domain.create(author.principal, "DEMO-A", content, "identity-concurrent-create")
        reviewed = domain.review(reviewer.principal, "DEMO-A", draft["id"], draft["version"],
                                 draft["payload_hash"], "identity-concurrent-review")
        approved = domain.approve(reviewer.principal, "DEMO-A", reviewed["id"], reviewed["version"],
                                  reviewed["payload_hash"], "identity-concurrent-approve")
        author_locked = Event()
        allow_approver_check = Event()
        switch_started = Event()
        original_require = self.identity.require

        def controlled_require(principal, action, connection, company_id=None):
            result = original_require(principal, action, connection, company_id)
            if action == "document.post":
                author_locked.set()
                if not allow_approver_check.wait(timeout=5):
                    raise AssertionError("post synchronization timed out")
            return result

        def switch_reviewer():
            switch_started.set()
            return self.identity.switch(reviewer.token, "dev-operator-a", reviewer.csrf_token,
                                        reviewer.principal.identity_revision)

        with ThreadPoolExecutor(max_workers=2) as pool, patch.object(self.identity, "require", controlled_require):
            posted = pool.submit(domain.post, author.principal, "DEMO-A", approved["id"], approved["version"],
                                 approved["payload_hash"], "identity-concurrent-post")
            try:
                self.assertTrue(author_locked.wait(timeout=3))
                switched = pool.submit(switch_reviewer)
                self.assertTrue(switch_started.wait(timeout=3))
                # B cannot acquire its account while A's post owns the common
                # transaction order, so A can still check B's approval rights.
                with self.assertRaises(TimeoutError):
                    switched.result(timeout=0.1)
            finally:
                allow_approver_check.set()
            self.assertEqual(posted.result(timeout=5)["status"], "POSTED")
            self.assertEqual(switched.result(timeout=5).principal.actor_id, author.principal.actor_id)
        with self.db.connect() as conn:
            event = conn.execute("SELECT actor_id FROM ledger_events WHERE document_id=%s", (approved["id"],)).fetchone()
        self.assertEqual(event["actor_id"], author.principal.actor_id)
    def test_migrations_repeat_safely_and_reject_changed_checksum(self):
        self.assertEqual(self.db.migrate(), [])
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "999_identity_test.sql"
            target.write_text("CREATE TABLE identity_migration_probe (id integer PRIMARY KEY);")
            self.assertEqual(self.db.migrate(Path(directory)), [target.name])
            target.write_text("CREATE TABLE another_identity_migration_probe (id integer PRIMARY KEY);")
            self.assertCode("MIGRATION_CHECKSUM_MISMATCH", self.db.migrate, Path(directory))


if __name__ == "__main__":
    unittest.main()
