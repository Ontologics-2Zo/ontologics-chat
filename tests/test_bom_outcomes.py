"""BOM reviewer outcomes (DEC-H48); TEST_DATABASE_URL must be disposable."""
import os
import unittest

from service.bom_contracts import BomReviewerOutcome, BomRevise
from service.contracts import DocumentAction
from service.errors import ServiceError
import tests.test_bom as bom_fixtures


@unittest.skipUnless(os.environ.get("TEST_DATABASE_URL"), "Dedicated disposable TEST_DATABASE_URL required")
class BomOutcomeTests(unittest.TestCase):
    def setUp(self):
        self.f = bom_fixtures.BomDomainTests("runTest")
        self.addCleanup(self.f.doCleanups)
        self.f.setUp()

    def outcome(self, action, actor, document, key, reason="원문 행 근거 메모를 보완해 주세요"):
        body = BomReviewerOutcome(expected_version=document["version"], payload_hash=document["payload_hash"],
                                  idempotency_key=key, reason=reason)
        return getattr(self.f.bom, action)(actor, self.f.company, document["id"], body)

    def revise(self, document, key):
        rows = [dict(row_id=row["row_id"], **row["decision"]) for row in document["content"]["rows"]]
        body = BomRevise(expected_version=document["version"], payload_hash=document["payload_hash"], idempotency_key=key,
                         expected_ledger_version=1, review_note="보완 요청을 반영한 검수 초안", rows=rows)
        return self.f.bom.revise(self.f.author, self.f.company, document["id"], body)

    def assert_code(self, code, call):
        with self.assertRaises(ServiceError) as caught:
            call()
        self.assertEqual(caught.exception.code, code)

    def ledger_version(self):
        with self.f.db.connect() as conn:
            return conn.execute("SELECT version FROM ledger_state WHERE company_id=%s", (self.f.company,)).fetchone()["version"]

    def test_change_request_returns_reviewed_draft_and_revision_clears_it(self):
        reviewed = self.f.action("review", self.f.reviewer, self.f.revised(), "review",
                                 review_note="Source rows checked; no physical technical approval", acknowledge_unresolved=True)
        returned = self.outcome("request_changes", self.f.reviewer, reviewed, "changes")
        self.assertEqual(returned["status"], "DRAFT")
        self.assertIsNone(returned["reviewed_by"])
        self.assertEqual(returned["change_request"]["previous_status"], "REVIEWED")
        self.assertEqual(self.outcome("request_changes", self.f.reviewer, reviewed, "changes")["id"], returned["id"])
        self.assert_code("REVIEW_REQUIRED", lambda: self.f.action("approve", self.f.reviewer, returned, "approve-after-return"))
        revised = self.revise(returned, "revise-after-return")
        self.assertIsNone(revised["change_request"])
        self.assertEqual(revised["version"], returned["version"] + 1)
        self.assertEqual(self.ledger_version(), 1)

    def test_reject_is_terminal_and_author_cannot_judge_own_document(self):
        draft = self.f.revised()
        self.assert_code("SELF_REVIEW_FORBIDDEN", lambda: self.outcome("reject", self.f.author, draft, "self-reject"))
        self.assert_code("PERMISSION_DENIED", lambda: self.outcome("reject", self.f.viewer, draft, "viewer-reject"))
        rejected = self.outcome("reject", self.f.reviewer, draft, "reject", reason="구성 식별 근거가 맞지 않아 반려합니다")
        self.assertEqual(rejected["status"], "REJECTED")
        self.assertEqual(rejected["rejection"]["reason"], "구성 식별 근거가 맞지 않아 반려합니다")
        self.assert_code("DOCUMENT_STATE_CHANGED", lambda: self.outcome("request_changes", self.f.reviewer, rejected, "after-reject"))
        self.assert_code("BOM_DOCUMENT_REJECTED", lambda: self.revise(rejected, "revise-after-reject"))
        self.assertEqual(self.ledger_version(), 1)

    def test_posted_document_cannot_be_returned(self):
        posted = self.f.action("post", self.f.author, self.f.approved(), "post")
        self.assert_code("DOCUMENT_STATE_CHANGED", lambda: self.outcome("request_changes", self.f.reviewer, posted, "posted"))
