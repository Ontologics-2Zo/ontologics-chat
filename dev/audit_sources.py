"""Read-only T00 source integrity and literal-ID audit; never emits source paths.

This checks the supplied manifest and public ID inventory, not narrative requirement
coverage, ontology correctness, service execution, or completion of T00.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat


MAX_METADATA_BYTES = 8 * 1024 * 1024
MAX_SOURCE_BYTES = 128 * 1024 * 1024
MAX_ENTRIES = 10000
HASH = re.compile(r"[0-9a-f]{64}\Z")


class AuditError(Exception):
    """Only fixed, non-sensitive error codes may leave the reader."""


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise AuditError("DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def _json(data):
    try:
        return json.loads(data, object_pairs_hook=_pairs)
    except (ValueError, UnicodeError, RecursionError):
        raise AuditError("INVALID_JSON") from None


def _parts(name):
    if not isinstance(name, str) or not name or "\\" in name or "\0" in name:
        raise AuditError("UNSAFE_PATH")
    parts = name.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise AuditError("UNSAFE_PATH")
    return parts


def _read(root_fd, name, limit, *, retain=True):
    """Open every component relative to an anchored directory, without symlinks."""
    parts = _parts(name)
    directory = os.dup(root_fd)
    file_fd = None
    try:
        for part in parts[:-1]:
            following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=directory)
            os.close(directory)
            directory = following
        file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                          dir_fd=directory)
        before = os.fstat(file_fd)
        if not stat.S_ISREG(before.st_mode):
            raise AuditError("NOT_REGULAR_FILE")
        if before.st_size > limit:
            raise AuditError("SIZE_LIMIT")
        digest = hashlib.sha256()
        chunks = []
        count = 0
        while True:
            chunk = os.read(file_fd, min(1024 * 1024, limit - count + 1))
            if not chunk:
                break
            count += len(chunk)
            if count > limit:
                raise AuditError("SIZE_LIMIT")
            digest.update(chunk)
            if retain:
                chunks.append(chunk)
        after = os.fstat(file_fd)
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_size, after.st_mtime_ns, after.st_ctime_ns) or count != after.st_size:
            raise AuditError("SOURCE_CHANGED_DURING_READ")
        return b"".join(chunks), digest.hexdigest(), count
    except OSError:
        raise AuditError("SOURCE_UNAVAILABLE_OR_UNSAFE") from None
    finally:
        if file_fd is not None:
            os.close(file_fd)
        os.close(directory)


def _entry_valid(entry):
    return (isinstance(entry, dict) and isinstance(entry.get("path"), str)
            and isinstance(entry.get("sha256"), str) and HASH.fullmatch(entry["sha256"])
            and type(entry.get("bytes")) is int and 0 <= entry["bytes"] <= MAX_SOURCE_BYTES)


def _inventory(root_fd, inventory, entries, source_records):
    result = {"status": "FAIL", "declared_ids": 0, "verified_ids": 0,
              "declared_locations": 0, "verified_locations": 0,
              "coverage": "literal_locations_only", "error_codes": []}
    if (not isinstance(inventory, dict) or not isinstance(inventory.get("files"), list)
            or not inventory["files"] or not isinstance(inventory.get("index"), dict)
            or not inventory["index"]):
        result["error_codes"] = ["INVALID_INVENTORY"]
        return result
    index = inventory["index"]
    result["declared_ids"] = len(index)
    if (type(inventory.get("unique_literal_ids")) is not int
            or inventory["unique_literal_ids"] != len(index) or len(index) > MAX_ENTRIES):
        result["error_codes"].append("INVENTORY_COUNT_MISMATCH")
    sources = {}
    for source in inventory["files"]:
        if (not isinstance(source, dict) or not isinstance(source.get("file"), str)
                or not isinstance(source.get("sha256"), str) or not HASH.fullmatch(source["sha256"])
                or type(source.get("lines")) is not int or source["lines"] < 0):
            result["error_codes"].append("INVALID_INVENTORY_FILE")
            continue
        name = source["file"]
        if name in sources:
            result["error_codes"].append("DUPLICATE_INVENTORY_FILE")
            continue
        # Match an inventory basename and its public hash, never an arbitrary path.
        matches = [e for e in entries if _entry_valid(e)
                   and PurePosixPath(e["path"]).name == name and e["sha256"] == source["sha256"]]
        if len(matches) != 1 or source_records.get(matches[0]["path"]) != "PASS":
            result["error_codes"].append("INVENTORY_SOURCE_NOT_VERIFIED")
            continue
        try:
            data, digest, _ = _read(root_fd, matches[0]["path"], MAX_SOURCE_BYTES)
            lines = data.decode("utf-8").splitlines()
            if digest != source["sha256"] or len(lines) != source["lines"]:
                raise AuditError("INVENTORY_SOURCE_MISMATCH")
            sources[name] = lines
        except (AuditError, UnicodeError):
            result["error_codes"].append("INVENTORY_SOURCE_MISMATCH")
    for literal, locations in index.items():
        if not isinstance(literal, str) or not literal or not isinstance(locations, list) or not locations:
            result["error_codes"].append("INVALID_INVENTORY_LOCATION")
            continue
        result["declared_locations"] += len(locations)
        verified = 0
        pattern = re.compile(r"(?<![A-Za-z0-9_-])" + re.escape(literal) + r"(?![A-Za-z0-9_-])")
        for loc in locations:
            if not isinstance(loc, dict) or not isinstance(loc.get("file"), str) or type(loc.get("line")) is not int:
                result["error_codes"].append("INVALID_INVENTORY_LOCATION")
                continue
            lines = sources.get(loc["file"], [])
            line = loc["line"]
            if 1 <= line <= len(lines) and pattern.search(lines[line - 1]):
                verified += 1
            else:
                result["error_codes"].append("LITERAL_LOCATION_MISMATCH")
        result["verified_locations"] += verified
        if verified == len(locations):
            result["verified_ids"] += 1
    result["error_codes"] = sorted(set(result["error_codes"]))
    if not result["error_codes"] and result["verified_ids"] == result["declared_ids"]:
        result["status"] = "PASS"
    return result


def audit_sources(private_root, inventory_path=None, *, repository_root=None):
    """Return metadata only. Input files and inventory are never modified."""
    report = {"schema_version": "1", "status": "FAIL", "t00_complete": False,
              "scope": "source_integrity_and_literal_id_correspondence",
              "manifest_sha256": None, "source_count": 0, "verified_source_count": 0,
              "source_bytes": 0, "sources": [], "error_codes": [],
              "requirements": {"status": "NOT_RUN", "coverage": "literal_locations_only"}}
    root_fd = None
    try:
        root = Path(private_root).resolve(strict=True)
        if repository_root is not None and root.is_relative_to(Path(repository_root).resolve()):
            raise AuditError("PRIVATE_ROOT_INSIDE_REPOSITORY")
        root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        data, manifest_hash, _ = _read(root_fd, "SOURCE_MANIFEST.json", MAX_METADATA_BYTES)
        report["manifest_sha256"] = manifest_hash
        manifest = _json(data)
        if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), list):
            raise AuditError("INVALID_MANIFEST")
        entries = manifest["files"]
        if not entries or len(entries) > MAX_ENTRIES:
            raise AuditError("INVALID_MANIFEST_COUNT")
        report["source_count"] = len(entries)
        seen = set()
        source_records = {}
        for number, entry in enumerate(entries):
            record = {"entry": number, "status": "FAIL"}
            report["sources"].append(record)
            try:
                if not _entry_valid(entry):
                    raise AuditError("INVALID_MANIFEST_ENTRY")
                name = entry["path"]
                _parts(name)
                if name in seen:
                    source_records[name] = "FAIL"
                    raise AuditError("DUPLICATE_MANIFEST_PATH")
                seen.add(name)
                record["expected_sha256"] = entry["sha256"]
                _, digest, count = _read(root_fd, name, MAX_SOURCE_BYTES, retain=False)
                record.update(actual_sha256=digest, bytes=count)
                report["source_bytes"] += count
                if digest != entry["sha256"] or count != entry["bytes"]:
                    raise AuditError("SOURCE_HASH_OR_SIZE_MISMATCH")
                record["status"] = "PASS"
                source_records[name] = "PASS"
                report["verified_source_count"] += 1
            except AuditError as exc:
                record["error_code"] = str(exc)
                report["error_codes"].append(str(exc))
        if inventory_path is not None:
            # The public inventory is also opened without following a final symlink.
            inventory_path = Path(inventory_path).absolute()
            parent_fd = os.open(inventory_path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                data, digest, _ = _read(parent_fd, inventory_path.name, MAX_METADATA_BYTES)
            finally:
                os.close(parent_fd)
            report["inventory_sha256"] = digest
            report["requirements"] = _inventory(root_fd, _json(data), entries, source_records)
            if report["requirements"]["status"] != "PASS":
                report["error_codes"].append("REQUIREMENT_INVENTORY_MISMATCH")
        if not report["error_codes"]:
            report["status"] = "PASS"
    except AuditError as exc:
        report["error_codes"].append(str(exc))
    except (OSError, ValueError, TypeError):
        report["error_codes"].append("AUDIT_INPUT_UNAVAILABLE")
    finally:
        if root_fd is not None:
            os.close(root_fd)
    report["error_codes"] = sorted(set(report["error_codes"]))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-root", default=os.environ.get("PRIVATE_INPUT_ROOT"))
    parser.add_argument("--inventory", default=str(Path(__file__).resolve().parents[1] / "docs/handoff/REQUIREMENT_ID_INVENTORY.json"))
    args = parser.parse_args()
    report = audit_sources(args.private_root, args.inventory,
                           repository_root=Path(__file__).resolve().parents[1])
    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
