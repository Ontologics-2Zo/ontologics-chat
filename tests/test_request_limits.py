"""Real HTTP middleware with synthetic request bodies and no DB or Runner.

The sink replaces domain routes only: these checks prove framing and size
boundaries, not BOM approval, persistence, or private-source acceptance.
"""

import asyncio
import json
import unittest
from unittest.mock import patch

from fastapi import Request

from service.bom_contracts import BomDecision, BomRevise
from service.main import create_app


ORIGIN = "http://127.0.0.1:8870"
BOM_ROUTE = "/api/mks2/bom/documents/12345678-1234-1234-1234-123456789abc/revise"


def revision_body(count, note=""):
    value = BomRevise(
        expected_version=1, payload_hash="a" * 64,
        idempotency_key="synthetic-request-size", expected_ledger_version=1,
        review_note="Independent synthetic whole-row review",
        rows=[BomDecision(row_id=f"{index:064x}", evidence_note=note)
              for index in range(count)],
    )
    # Match browser JSON.stringify: UTF-8, not ASCII-escaped Korean text.
    return json.dumps(value.model_dump(), ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")


class RequestLimitTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        with patch("service.main.ontology_view", return_value={}):
            self.app = create_app(object(), private_root="/unused-synthetic-root",
                                  catalog=object(), bom_runner=object())
        self.app.router.routes.clear()
        self.received = []

        @self.app.post(BOM_ROUTE)
        async def revise(body: BomRevise):
            self.received.append(body)
            return {"row_count": len(body.rows)}

        @self.app.post("/api/mks2/documents")
        async def ordinary(request: Request):
            body = await request.body()
            self.received.append(body)
            return {"bytes": len(body)}

    async def request(self, chunks, *, path=BOM_ROUTE, method="POST", lengths=None):
        messages, response_complete = [], asyncio.Event()
        bodies = list(chunks)
        if lengths is None:
            lengths = [str(sum(map(len, bodies)))]
        headers = [(b"host", b"127.0.0.1:8870"), (b"origin", ORIGIN.encode()),
                   (b"content-type", b"application/json"),
                   (b"x-requested-with", b"MK-S2")]
        headers.extend((b"content-length", value.encode("latin-1")) for value in lengths)
        index = 0

        async def receive():
            nonlocal index
            if index < len(bodies):
                index += 1
                return {"type": "http.request", "body": bodies[index - 1],
                        "more_body": index < len(bodies)}
            await response_complete.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            messages.append(message)
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                response_complete.set()

        scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
                 "http_version": "1.1", "scheme": "http", "method": method,
                 "path": path, "raw_path": path.encode(), "root_path": "",
                 "query_string": b"", "headers": headers,
                 "server": ("127.0.0.1", 8870), "client": ("127.0.0.1", 40000)}
        await asyncio.wait_for(self.app(scope, receive, send), timeout=5)
        start = next(message for message in messages if message["type"] == "http.response.start")
        data = b"".join(message.get("body", b"") for message in messages
                        if message["type"] == "http.response.body")
        return start["status"], json.loads(data), dict(start["headers"])

    async def assert_rejected(self, chunks, **kwargs):
        status, data, headers = await self.request(chunks, **kwargs)
        self.assertEqual(status, 413)
        self.assertEqual(data, {"error": {"code": "REQUEST_SIZE_REJECTED"}})
        self.assertEqual(headers[b"cache-control"], b"no-store")
        self.assertIn(b"content-security-policy", headers)
        self.assertEqual(self.received, [])

    async def test_74_row_korean_review_passes_the_real_bom_contract(self):
        body = revision_body(74, ("검토 근거 확인 " * 150).strip())
        self.assertGreater(len(body), 128 * 1024)
        self.assertLess(len(body), 2 * 1024 * 1024)
        status, data, _ = await self.request([body[:50000], body[50000:]])
        self.assertEqual((status, data), (200, {"row_count": 74}))
        self.assertEqual(len(self.received), 1)

    async def test_650_row_revision_no_longer_hits_generic_limit(self):
        body = revision_body(650)
        self.assertGreater(len(body), 128 * 1024)
        status, data, _ = await self.request([body])
        self.assertEqual((status, data), (200, {"row_count": 650}))

    async def test_other_routes_and_methods_keep_128_kib_limit(self):
        body = b" " * (128 * 1024 + 1)
        for path, method in (("/api/mks2/documents", "POST"),
                             (BOM_ROUTE.replace("/revise", "/review"), "POST"),
                             (BOM_ROUTE + "/extra", "POST"),
                             (BOM_ROUTE, "PATCH")):
            with self.subTest(path=path, method=method):
                await self.assert_rejected([body], path=path, method=method)

    async def test_declared_bom_size_above_2_mib_is_rejected(self):
        await self.assert_rejected([b"{}"], lengths=[str(2 * 1024 * 1024 + 1)])

    async def test_stream_cannot_exceed_its_route_limit(self):
        await self.assert_rejected([b" " * (1024 * 1024), b" " * (1024 * 1024), b"x"],
                                   lengths=[str(2 * 1024 * 1024)])
        await self.assert_rejected([b" " * (128 * 1024), b"x"],
                                   path="/api/mks2/documents", lengths=[str(128 * 1024)])

    async def test_missing_duplicate_or_malformed_content_length_is_rejected(self):
        for lengths in ([], ["2", "2"], ["-1"], ["+2"], ["2.0"], [" 2"],
                        ["2x"], ["²"], ["9" * 5000]):
            with self.subTest(length_count=len(lengths), prefix=str(lengths)[:20]):
                await self.assert_rejected([b"{}"], lengths=lengths)

    async def test_actual_length_must_match_declared_length(self):
        for declared in ("1", "3"):
            with self.subTest(declared=declared):
                await self.assert_rejected([b"{", b"}"], lengths=[declared])

    async def test_larger_allowance_still_rejects_malformed_json(self):
        status, data, _ = await self.request([b"[" + b" " * (128 * 1024)])
        self.assertEqual((status, data), (422, {"error": {"code": "INVALID_REQUEST"}}))
        self.assertEqual(self.received, [])


if __name__ == "__main__":
    unittest.main()
