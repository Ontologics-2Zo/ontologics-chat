"""Security boundaries for the local visual preview; no private fixtures used."""

from contextlib import contextmanager
from http.client import HTTPConnection
import importlib.util
import json
from pathlib import Path
import tempfile
from threading import Thread
import unittest


spec = importlib.util.spec_from_file_location("preview_server", Path(__file__).resolve().parents[1] / "dev" / "preview_server.py")
preview = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preview)


class PreviewBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.repo = self.base / "repo"
        self.web = self.repo / "web"
        self.web.mkdir(parents=True)
        (self.web / "index.html").write_text("<!doctype html><title>Independent test fixture</title>")
        (self.web / "shell.html").write_text('<!doctype html><iframe src="/current/"></iframe><iframe src="/mks2/"></iframe>')
        (self.web / "shell.css").write_text("/* shell styles */")
        (self.web / "shell.js").write_text("// shell behavior")
        (self.web / "mks2").mkdir()
        (self.web / "mks2" / "index.html").write_text('<!doctype html><title>Separate MK-S2</title><script type="module" src="./app.js"></script>')
        for filename in ("app.js", "styles.css", "dashboard.js", "dashboard.css", "graph.js", "graph.css"):
            (self.web / filename).write_text(f"/* preserved current {filename} */")
            (self.web / "mks2" / filename).write_text(f"/* separate MK-S2 {filename} */")
        (self.repo / "private.txt").write_text("MUST_NOT_BE_SERVED")
        self.private = self.base / "private"
        fixture_root = self.private / "mk-s2" / "fixtures"
        fixture_root.mkdir(parents=True)
        for name, filename in preview.FIXTURE_FILES.items():
            (fixture_root / filename).write_text(json.dumps({"synthetic_test_key": name}))

    def tearDown(self):
        self.temp.cleanup()

    @contextmanager
    def running(self, enabled=True, root="default"):
        server = preview.PreviewServer(
            0, reference_preview=enabled,
            private_root=str(self.private) if root == "default" else root,
            web_root=self.web, repo_root=self.repo,
        )
        thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        try:
            yield server
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def request(self, server, path="/api/preview", method="GET", headers=None, body=None):
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=2)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_explicit_preview_reads_only_fixed_files_without_mutation(self):
        paths = list(self.private.rglob("*.json"))
        before = {p: p.read_bytes() for p in paths}
        with self.running() as server:
            status, headers, body = self.request(server)
        result = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(result["mode"], "reference-preview")
        self.assertFalse(result["serviceConnected"])
        self.assertEqual(set(result["bundle"]), set(preview.FIXTURE_FILES))
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertIn("connect-src 'self'", headers["Content-Security-Policy"])
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        self.assertEqual(before, {p: p.read_bytes() for p in paths})

    def test_disabled_or_missing_private_input_never_substitutes_fixture(self):
        for enabled, root, expected in ((False, "default", 409), (True, None, 503)):
            with self.subTest(enabled=enabled, root=root), self.running(enabled, root) as server:
                status, _, body = self.request(server)
                self.assertEqual(status, expected)
                self.assertNotIn("bundle", json.loads(body))
                self.assertNotIn(str(self.base).encode(), body)

    def test_private_root_inside_repo_and_escape_symlink_rejected(self):
        with self.running(root=str(self.repo)) as server:
            self.assertEqual(self.request(server)[0], 503)
        first = self.private / "mk-s2" / "fixtures" / next(iter(preview.FIXTURE_FILES.values()))
        first.unlink()
        first.symlink_to(self.repo / "private.txt")
        with self.running() as server:
            status, _, body = self.request(server)
            self.assertEqual(status, 503)
            self.assertNotIn(b"MUST_NOT_BE_SERVED", body)

    def test_unknown_paths_traversal_queries_and_static_symlinks_rejected(self):
        paths = ["/../private.txt", "/%2e%2e/private.txt", "/private_inputs/", "/fixtures/00_session.json", "/api/preview?file=private.txt", "/.env", "/%5c..%5cprivate.txt", "/mks2/../app.js", "/mks2/%2e%2e/app.js", "/mks2/private.txt"]
        (self.web / "mks2" / "private.txt").write_text("MUST_NOT_BE_SERVED")
        with self.running() as server:
            for path in paths:
                with self.subTest(path=path):
                    status, _, body = self.request(server, path)
                    self.assertEqual(status, 404)
                    self.assertNotIn(b"MUST_NOT_BE_SERVED", body)
            self.assertEqual(self.request(server, "/")[0], 200)
            (self.web / "app.js").unlink()
            (self.web / "app.js").symlink_to(self.repo / "private.txt")
            self.assertEqual(self.request(server, "/app.js")[0], 404)

    def test_shell_current_and_mks2_routes_remain_separate(self):
        with self.running() as server:
            status, _, shell = self.request(server, "/")
            self.assertEqual(status, 200)
            self.assertEqual(shell, (self.web / "shell.html").read_bytes())
            for path in ("/current/", "/index.html"):
                self.assertEqual(self.request(server, path)[2], (self.web / "index.html").read_bytes())
            for path in ("/mks2/", "/mks2/index.html"):
                self.assertEqual(self.request(server, path)[2], (self.web / "mks2" / "index.html").read_bytes())
            for filename in ("app.js", "styles.css", "dashboard.js", "dashboard.css", "graph.js", "graph.css"):
                with self.subTest(filename=filename):
                    current = self.request(server, "/" + filename)
                    separate = self.request(server, "/mks2/" + filename)
                    self.assertEqual((current[0], separate[0]), (200, 200))
                    self.assertEqual(current[2], (self.web / filename).read_bytes())
                    self.assertEqual(separate[2], (self.web / "mks2" / filename).read_bytes())
                    self.assertNotEqual(current[2], separate[2])
            for filename in ("shell.css", "shell.js"):
                self.assertEqual(self.request(server, "/" + filename)[0], 200)

    def test_same_origin_frames_allowed_without_external_frame_access(self):
        with self.running() as server:
            for path in ("/", "/current/", "/mks2/"):
                with self.subTest(path=path):
                    status, headers, _ = self.request(server, path, headers={"Sec-Fetch-Site": "same-origin", "Sec-Fetch-Dest": "iframe"})
                    self.assertEqual(status, 200)
                    self.assertEqual(headers["X-Frame-Options"], "SAMEORIGIN")
                    directives = {part.strip() for part in headers["Content-Security-Policy"].split(";")}
                    self.assertIn("frame-src 'self'", directives)
                    self.assertIn("frame-ancestors 'self'", directives)
                    self.assertEqual(self.request(server, path, headers={"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Dest": "iframe"})[0], 403)

    def test_twin_assets_are_individually_allowlisted_without_exposing_vendor_directory(self):
        files = {"twin2.js": "text/javascript", "twin2.css": "text/css",
                 "vendor/three/three.module.min.js": "text/javascript",
                 "vendor/three/OrbitControls.js": "text/javascript",
                 "vendor/three/CSS2DRenderer.js": "text/javascript"}
        for filename in files:
            asset = self.web / "mks2" / filename
            asset.parent.mkdir(parents=True, exist_ok=True)
            asset.write_text("/* independent synthetic asset */")
        (self.web / "mks2/vendor/three/unlisted.js").write_text("MUST_NOT_BE_SERVED")
        with self.running() as server:
            for filename, mime in files.items():
                with self.subTest(filename=filename):
                    status, headers, content = self.request(server, "/mks2/" + filename)
                    self.assertEqual(status, 200)
                    self.assertTrue(headers["Content-Type"].startswith(mime))
                    self.assertEqual(content, (self.web / "mks2" / filename).read_bytes())
            for path in ("/mks2/vendor/", "/mks2/vendor/three/", "/mks2/vendor/three/unlisted.js",
                         "/mks2/twin2.js?file=private.txt", "/mks2/vendor/three/%2e%2e/twin2.js", "/mks2/twin.js"):
                self.assertEqual(self.request(server, path)[0], 404)

    def test_writes_rejected_and_cross_origin_cannot_read_private_data(self):
        with self.running() as server:
            for method in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE"):
                with self.subTest(method=method):
                    status, _, body = self.request(server, method=method, body="SECRET_TEST_BODY")
                    self.assertEqual(status, 405)
                    self.assertNotIn(b"SECRET_TEST_BODY", body)
            for headers in ({"Host": "attacker.invalid"}, {"Origin": "https://attacker.invalid"}, {"Sec-Fetch-Site": "cross-site"}):
                with self.subTest(headers=headers):
                    status, _, body = self.request(server, headers=headers)
                    self.assertEqual(status, 403)
                    self.assertNotIn(b"synthetic_test_key", body)

    def test_malformed_fixture_error_does_not_leak_contents_or_path(self):
        first = self.private / "mk-s2" / "fixtures" / next(iter(preview.FIXTURE_FILES.values()))
        first.write_text("PRIVATE_INVALID_JSON")
        with self.running() as server:
            status, _, body = self.request(server)
            self.assertEqual(status, 503)
            self.assertNotIn(b"PRIVATE_INVALID_JSON", body)
            self.assertNotIn(str(first).encode(), body)


if __name__ == "__main__":
    unittest.main()
