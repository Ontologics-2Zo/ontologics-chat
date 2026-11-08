"""Independent synthetic files exercise integrity, path and reporting boundaries."""

import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dev.audit_sources import audit_sources


class SourceAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "private"
        self.root.mkdir()
        self.source = self.root / "specs" / "requirements.md"
        self.source.parent.mkdir()
        self.content = b"Requirement REQ-01 applies.\nBoundary REQ-010 differs.\n"
        self.source.write_bytes(self.content)
        self.entry = {"path": "specs/requirements.md", "bytes": len(self.content),
                      "sha256": hashlib.sha256(self.content).hexdigest()}
        self.manifest = self.root / "SOURCE_MANIFEST.json"
        self.write_manifest([self.entry])
        self.inventory_path = self.base / "inventory.json"
        self.inventory = {"unique_literal_ids": 1,
                          "files": [{"file": "requirements.md", "sha256": self.entry["sha256"], "lines": 2}],
                          "index": {"REQ-01": [{"file": "requirements.md", "line": 1}]}}
        self.write_inventory()

    def write_manifest(self, entries):
        self.manifest.write_text(json.dumps({"schema_version": "1", "files": entries}))

    def write_inventory(self):
        self.inventory_path.write_text(json.dumps(self.inventory))

    def run_audit(self):
        result = audit_sources(self.root, self.inventory_path)
        rendered = json.dumps(result)
        self.assertNotIn(str(self.root), rendered)
        self.assertNotIn("requirements.md", rendered)
        self.assertNotIn("Requirement REQ-01 applies", rendered)
        self.assertIs(result["t00_complete"], False)
        return result

    def test_complete_manifest_and_literal_locations_preserve_input_bytes(self):
        before = {p: p.read_bytes() for p in (self.source, self.manifest, self.inventory_path)}
        result = self.run_audit()
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["source_count"], 1)
        self.assertEqual(result["verified_source_count"], 1)
        self.assertEqual(result["sources"][0]["actual_sha256"], self.entry["sha256"])
        self.assertEqual(result["requirements"]["verified_ids"], 1)
        self.assertEqual(result["requirements"]["verified_locations"], 1)
        self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_tampered_and_missing_sources_fail_without_leaking_paths(self):
        for mode in ("tamper", "missing"):
            with self.subTest(mode=mode):
                if mode == "tamper":
                    self.source.write_bytes(b"different private bytes")
                else:
                    self.source.unlink()
                result = self.run_audit()
                self.assertEqual(result["status"], "FAIL")
                self.assertEqual(result["verified_source_count"], 0)
                self.assertEqual(result["requirements"]["verified_locations"], 0)

    def test_every_manifest_entry_is_checked_after_a_failure(self):
        self.write_manifest([{**self.entry, "path": "missing-secret-name"}, self.entry])
        result = self.run_audit()
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(len(result["sources"]), 2)
        self.assertEqual(result["verified_source_count"], 1)
        self.assertNotIn("missing-secret-name", json.dumps(result))

    def test_unsafe_paths_never_escape_root(self):
        for path in ("../outside", "/etc/passwd", "specs/../requirements.md", "specs//requirements.md", "specs\\requirements.md", "a\0b"):
            with self.subTest(path=path):
                self.write_manifest([{**self.entry, "path": path}])
                result = self.run_audit()
                self.assertIn("UNSAFE_PATH", result["error_codes"])
                self.assertEqual(result["verified_source_count"], 0)

    def test_file_and_directory_symlinks_are_rejected(self):
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "payload").write_bytes(self.content)
        (self.root / "file-link").symlink_to(outside / "payload")
        (self.root / "dir-link").symlink_to(outside, target_is_directory=True)
        for path in ("file-link", "dir-link/payload"):
            with self.subTest(path=path):
                self.write_manifest([{**self.entry, "path": path}])
                result = self.run_audit()
                self.assertEqual(result["status"], "FAIL")
                self.assertEqual(result["verified_source_count"], 0)

    def test_fifo_is_rejected_without_blocking(self):
        os.mkfifo(self.root / "pipe")
        self.write_manifest([{**self.entry, "path": "pipe"}])
        self.assertIn("NOT_REGULAR_FILE", self.run_audit()["error_codes"])

    def test_manifest_symlink_is_rejected(self):
        destination = self.base / "outside-manifest.json"
        self.manifest.replace(destination)
        self.manifest.symlink_to(destination)
        result = self.run_audit()
        self.assertEqual(result["source_count"], 0)
        self.assertIn("SOURCE_UNAVAILABLE_OR_UNSAFE", result["error_codes"])

    def test_oversized_file_is_not_read_to_completion(self):
        self.write_manifest([{**self.entry, "bytes": 1}])
        with patch("dev.audit_sources.MAX_SOURCE_BYTES", 16):
            result = self.run_audit()
        self.assertIn("SIZE_LIMIT", result["error_codes"])
        self.assertEqual(result["source_bytes"], 0)

    def test_duplicate_manifest_paths_and_json_keys_fail_closed(self):
        self.write_manifest([self.entry, self.entry])
        self.assertIn("DUPLICATE_MANIFEST_PATH", self.run_audit()["error_codes"])
        self.manifest.write_text('{"files": [], "files": []}')
        self.assertIn("DUPLICATE_JSON_KEY", self.run_audit()["error_codes"])

    def test_invalid_manifest_structures_are_sanitized(self):
        for value in ([], {}, {"files": []}, {"files": [None]}, {"files": [{**self.entry, "bytes": True}]}, {"files": [{**self.entry, "sha256": "bad"}]}):
            with self.subTest(value=value):
                self.manifest.write_text(json.dumps(value))
                self.assertEqual(self.run_audit()["status"], "FAIL")
        self.manifest.write_bytes(b"not-json: confidential text")
        self.assertIn("INVALID_JSON", self.run_audit()["error_codes"])

    def test_line_correspondence_and_token_boundaries_are_required(self):
        self.inventory["index"]["REQ-01"][0]["line"] = 2
        self.write_inventory()
        result = self.run_audit()
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["requirements"]["verified_ids"], 0)
        self.assertIn("LITERAL_LOCATION_MISMATCH", result["requirements"]["error_codes"])

    def test_inventory_hash_and_declared_count_must_match(self):
        self.inventory["files"][0]["sha256"] = "0" * 64
        self.inventory["unique_literal_ids"] = 2
        self.write_inventory()
        result = self.run_audit()
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("INVENTORY_SOURCE_NOT_VERIFIED", result["requirements"]["error_codes"])
        self.assertIn("INVENTORY_COUNT_MISMATCH", result["requirements"]["error_codes"])

    def test_root_inside_repository_is_refused(self):
        result = audit_sources(self.root, self.inventory_path, repository_root=self.base)
        self.assertIn("PRIVATE_ROOT_INSIDE_REPOSITORY", result["error_codes"])
        self.assertEqual(result["source_count"], 0)

    def test_missing_root_and_inventory_report_no_traceback_or_path(self):
        result = audit_sources(None, self.inventory_path)
        self.assertEqual(result["status"], "FAIL")
        self.inventory_path.unlink()
        self.assertIn("SOURCE_UNAVAILABLE_OR_UNSAFE", self.run_audit()["error_codes"])


if __name__ == "__main__":
    unittest.main()
