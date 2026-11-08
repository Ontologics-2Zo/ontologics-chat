#!/usr/bin/env python3
"""Read-only, local MK-S2 visual reference preview. This is not a domain API.

Private fixtures stay outside Git and are read only when --reference-preview is
explicitly selected. No ledger, approval, model, or observability calls occur.
"""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import stat
from urllib.parse import urlsplit


REPO_ROOT = Path(__file__).resolve().parents[1]
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/dashboard.js": ("dashboard.js", "text/javascript; charset=utf-8"),
    "/dashboard.css": ("dashboard.css", "text/css; charset=utf-8"),
    "/graph.js": ("graph.js", "text/javascript; charset=utf-8"),
    "/graph.css": ("graph.css", "text/css; charset=utf-8"),
}
FIXTURE_FILES = {
    "session": "00_session.json",
    "kpis": "01_dashboard.kpis.json",
    "rounds": "02_rounds.json",
    "production": "03_production.json",
    "planning": "04_planning.materials.json",
    "inventory": "05_inventory.snapshot.json",
    "purchase": "06_purchase.json",
    "bom": "07_bom.json",
    "ledger": "08_ledger.json",
    "graph": "09_graph.initial.json",
    "ai": "11_ai.agent.json",
    "graphQueries": "12_ai.graph_queries.json",
}
MAX_FIXTURE_BYTES = 2 * 1024 * 1024
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; font-src 'self'; connect-src 'self'; "
    "base-uri 'none'; form-action 'self'; frame-ancestors 'none'; object-src 'none'"
)


class PreviewUnavailable(Exception):
    """Carries only a fixed safe error code, never a private path or payload."""


class PreviewServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        port: int = 8765,
        *,
        reference_preview: bool = False,
        private_root: str | None = None,
        web_root: Path | None = None,
        repo_root: Path = REPO_ROOT,
    ) -> None:
        self.reference_preview = reference_preview
        self.private_root = private_root
        self.web_root = (web_root or REPO_ROOT / "web").resolve()
        self.repo_root = repo_root.resolve()
        super().__init__(("127.0.0.1", port), PreviewHandler)

    @property
    def mode(self) -> str:
        return "reference-preview" if self.reference_preview else "static-preview"

    def load_bundle(self) -> dict:
        if not self.reference_preview:
            raise PreviewUnavailable("REFERENCE_PREVIEW_DISABLED")
        if not self.private_root:
            raise PreviewUnavailable("PRIVATE_INPUT_UNAVAILABLE")
        try:
            root = Path(self.private_root).expanduser().resolve(strict=True)
            if not root.is_dir() or root.is_relative_to(self.repo_root):
                raise PreviewUnavailable("PRIVATE_INPUT_LOCATION_REJECTED")
            fixture_root = (root / "mk-s2" / "fixtures").resolve(strict=True)
            if not fixture_root.is_relative_to(root):
                raise PreviewUnavailable("PRIVATE_INPUT_LOCATION_REJECTED")
            bundle = {}
            for name, filename in FIXTURE_FILES.items():
                source = (fixture_root / filename).resolve(strict=True)
                if not source.is_relative_to(fixture_root):
                    raise PreviewUnavailable("PRIVATE_INPUT_LOCATION_REJECTED")
                info = source.stat()
                if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FIXTURE_BYTES:
                    raise PreviewUnavailable("REFERENCE_FIXTURE_INVALID")
                with source.open("rb") as stream:
                    content = stream.read(MAX_FIXTURE_BYTES + 1)
                if len(content) > MAX_FIXTURE_BYTES:
                    raise PreviewUnavailable("REFERENCE_FIXTURE_INVALID")
                bundle[name] = json.loads(content)
            return bundle
        except PreviewUnavailable:
            raise
        except (OSError, ValueError, UnicodeError, RuntimeError):
            raise PreviewUnavailable("REFERENCE_FIXTURE_UNAVAILABLE") from None

    def handle_error(self, request, client_address) -> None:
        # Base implementation prints arbitrary exception details to stderr.
        # This local preview deliberately emits no request or payload logs.
        pass


class PreviewHandler(BaseHTTPRequestHandler):
    server: PreviewServer
    server_version = "MK-S2-Visual-Preview"
    sys_version = ""

    def log_message(self, format, *args) -> None:
        pass

    def send_error(self, code, message=None, explain=None) -> None:
        self.respond_json(code, {"error": "HTTP_REQUEST_REJECTED"})

    def respond(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def respond_json(self, status: int, payload: dict) -> None:
        self.respond(status, json.dumps(payload, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def local_origin(self) -> bool:
        port = self.server.server_port
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if port == 80:
            hosts |= {"127.0.0.1", "localhost"}
        host_values = self.headers.get_all("Host", [])
        if len(host_values) != 1 or host_values[0] not in hosts:
            return False
        origins = self.headers.get_all("Origin", [])
        if origins and (len(origins) != 1 or origins[0] != f"http://{host_values[0]}"):
            return False
        return self.headers.get("Sec-Fetch-Site") not in {"cross-site", "same-site"}

    def do_GET(self) -> None:
        if not self.local_origin():
            self.respond_json(403, {"error": "LOCAL_ORIGIN_REQUIRED"})
            return
        # Exact paths prevent encoded traversal, alternate separators, or
        # filesystem URLs from entering any file-serving operation.
        try:
            parsed = urlsplit(self.path)
        except ValueError:
            self.respond_json(400, {"error": "INVALID_REQUEST_PATH"})
            return
        if parsed.scheme or parsed.netloc or parsed.fragment or parsed.query:
            self.respond_json(404, {"error": "NOT_FOUND"})
            return
        path = parsed.path
        if path == "/health":
            self.respond_json(200, {"status": "ok", "mode": self.server.mode, "serviceConnected": False})
            return
        if path == "/api/preview":
            try:
                bundle = self.server.load_bundle()
            except PreviewUnavailable as error:
                code = str(error)
                self.respond_json(409 if code == "REFERENCE_PREVIEW_DISABLED" else 503, {
                    "error": code, "mode": self.server.mode, "serviceConnected": False,
                })
                return
            self.respond_json(200, {"mode": "reference-preview", "serviceConnected": False, "bundle": bundle})
            return
        entry = STATIC_FILES.get(path)
        if entry is None:
            self.respond_json(404, {"error": "NOT_FOUND"})
            return
        filename, content_type = entry
        try:
            source = (self.server.web_root / filename).resolve(strict=True)
            if not source.is_relative_to(self.server.web_root) or not source.is_file():
                raise OSError
            body = source.read_bytes()
        except OSError:
            self.respond_json(404, {"error": "STATIC_FILE_UNAVAILABLE"})
            return
        self.respond(200, body, content_type)

    do_HEAD = do_GET

    def reject_write(self) -> None:
        self.close_connection = True
        self.respond_json(405, {"error": "READ_ONLY_PREVIEW"})

    do_POST = reject_write
    do_PUT = reject_write
    do_PATCH = reject_write
    do_DELETE = reject_write
    do_OPTIONS = reject_write
    do_TRACE = reject_write
    do_CONNECT = reject_write


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--reference-preview", action="store_true", help="Read the fixed private MK-S2 fixture allowlist")
    args = parser.parse_args()
    server = PreviewServer(args.port, reference_preview=args.reference_preview, private_root=os.environ.get("PRIVATE_INPUT_ROOT"))
    print(f"MK-S2 {server.mode}: http://127.0.0.1:{server.server_port} (local to this machine; service not connected)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
