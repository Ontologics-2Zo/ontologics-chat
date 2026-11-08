"""Local fixed-job Docker Broker. No raw BOM parser runs in this process.

The operator selects a built immutable image ID, not an HTTP/Agent argument.
Only a private, single input file is mounted; database/model credentials, the
repository, and Docker socket are never mounted or passed into the container.
"""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import re
import selectors
import subprocess
import tempfile
from threading import BoundedSemaphore
import time
from uuid import uuid4

from dev.service_stack import local_docker_command, SELECTORS, StackError
from service.errors import ServiceError


PARSER_VERSION = "bom-parser-v1"
MAX_INPUT = 8 * 1024 * 1024
MAX_OUTPUT = 2 * 1024 * 1024
MAX_STDERR = 64 * 1024
RUNNER_DIRECTORY = Path(__file__).resolve().parents[1] / "runner" / "bom"
_JOBS = BoundedSemaphore(2)


def runner_source_hash():
    digest = sha256()
    for name in ("Dockerfile", "requirements.lock", "parse.py", "probe.py"):
        digest.update(name.encode() + b"\x00" + (RUNNER_DIRECTORY / name).read_bytes() + b"\x00")
    return digest.hexdigest()


class BomRunner:
    def __init__(self, image_id=None, docker_socket=None, timeout_seconds=20):
        self.image_id = image_id or os.environ.get("ONTO_BOM_RUNNER_IMAGE", "")
        self.docker_socket = docker_socket or os.environ.get("ONTO_BOM_DOCKER_SOCKET", "/var/run/docker.sock")
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not 0 < timeout_seconds <= 60:
            raise ServiceError("RUNNER_CONFIGURATION_INVALID", 503)
        self.timeout_seconds = float(timeout_seconds)
        self.broker_id = uuid4().hex
        self._checked = False
        self._enforcement = None

    def _command(self):
        try:
            return local_docker_command(self.docker_socket)
        except StackError:
            raise ServiceError("RUNNER_UNAVAILABLE", 503) from None

    @staticmethod
    def _environment():
        return {key: value for key, value in os.environ.items() if key not in SELECTORS}

    def _check_image(self):
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", self.image_id):
            raise ServiceError("RUNNER_UNAVAILABLE", 503)
        try:
            result = subprocess.run(self._command() + ["image", "inspect", self.image_id, "--format", "{{json .}}"],
                                    capture_output=True, timeout=10, env=self._environment())
            if result.returncode or len(result.stdout) > 65536:
                raise ServiceError("RUNNER_UNAVAILABLE", 503)
            info = json.loads(result.stdout)
            labels = info.get("Config", {}).get("Labels") or {}
            if (info.get("Id") != self.image_id or labels.get("org.ontologics.runner.version") != PARSER_VERSION
                    or labels.get("org.ontologics.runner.source-sha256") != runner_source_hash()):
                raise ServiceError("RUNNER_IMAGE_MISMATCH", 503)
        except (OSError, ValueError, subprocess.SubprocessError):
            raise ServiceError("RUNNER_UNAVAILABLE", 503) from None

    def _check_resource_support(self):
        try:
            format_ = '{"driver":{{json .CgroupDriver}},"memory":{{json .MemoryLimit}},"pids":{{json .PidsLimit}},"cpu_period":{{json .CPUCfsPeriod}},"cpu_quota":{{json .CPUCfsQuota}}}'
            result = subprocess.run(self._command() + ["info", "--format", format_],
                                    capture_output=True, timeout=10, env=self._environment())
            if result.returncode or len(result.stdout) > 8192:
                raise ServiceError("RUNNER_UNAVAILABLE", 503)
            info = json.loads(result.stdout)
            if (info.get("driver") in {None, "", "none"}
                    or any(info.get(key) is not True for key in ("memory", "pids", "cpu_period", "cpu_quota"))):
                raise ServiceError("RUNNER_ENFORCEMENT_UNAVAILABLE", 503)
        except (OSError, ValueError, subprocess.SubprocessError):
            raise ServiceError("RUNNER_UNAVAILABLE", 503) from None

    def _arguments(self, name, path, script, arguments):
        argv = self._command() + [
            "run", "--rm", "--pull=never", "--name", name,
            "--label", "org.ontologics.job=bom-parser", "--label", "org.ontologics.broker=" + self.broker_id,
            "--network=none", "--ipc=none",
            "--read-only", "--user=65532:65532", "--cap-drop=ALL", "--security-opt=no-new-privileges",
            "--pids-limit=32", "--memory=256m", "--memory-swap=256m", "--cpus=0.5",
            "--ulimit", "nofile=64:64", "--ulimit", "fsize=4194304:4194304",
            "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=16777216,mode=1777",
            "--mount", "type=bind,src=" + str(path) + ",dst=/input/blob,readonly",
            "--workdir=/tmp", "--entrypoint=/usr/local/bin/python",
        ]
        # Docker client proxy defaults must not leak a proxy address/credential
        # into a network-disabled parser, even on managed development hosts.
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "all_proxy", "no_proxy"):
            argv.extend(["--env", key + "="])
        return argv + [self.image_id, "-I", "/opt/runner/" + script] + arguments

    def _remove(self, name):
        try:
            subprocess.run(self._command() + ["rm", "--force", name], capture_output=True,
                           timeout=10, env=self._environment())
            remaining = subprocess.run(self._command() + ["container", "inspect", name, "--format", "{{.Id}}"],
                                       capture_output=True, timeout=10, env=self._environment())
            missing = b"no such" in remaining.stderr.lower()
            if remaining.returncode == 0 or not missing:
                raise ServiceError("RUNNER_CLEANUP_FAILED", 503)
        except (OSError, subprocess.SubprocessError):
            raise ServiceError("RUNNER_CLEANUP_FAILED", 503) from None

    def _run(self, data, *, kind=None, probe=None, cancel_event=None):
        # This method is a Broker implementation detail. Neither script nor
        # docker options are accepted from a document or an API request.
        if (kind is None) == (probe is None) or kind not in {None, "csv", "ods", "pdf"} or probe not in {None, "basic", "enforcement", "timeout", "output"}:
            raise ServiceError("RUNNER_JOB_REJECTED", 422)
        name = "ontologics-bom-" + uuid4().hex
        process = None
        with tempfile.TemporaryDirectory(prefix="ontologics-bom-") as directory:
            path = Path(directory) / "input"
            if "," in str(path):
                raise ServiceError("RUNNER_CONFIGURATION_INVALID", 503)
            with path.open("xb") as stream:
                stream.write(data)
            path.chmod(0o444)
            command = self._arguments(name, path, "parse.py" if kind else "probe.py", [kind, "/input/blob"] if kind else [probe])
            try:
                if cancel_event is not None and cancel_event.is_set():
                    raise ServiceError("RUNNER_CANCELLED", 409)
                # Create before attach. A timed-out `docker run` can otherwise
                # finish creating a container *after* a cleanup-by-name probe.
                # With create/start, the exact container exists before the
                # parser execution deadline and cancellation polling begin.
                command[command.index("run")] = "create"
                command.remove("--rm")
                created = subprocess.run(command, capture_output=True, timeout=15, env=self._environment())
                if created.returncode or not re.fullmatch(rb"[0-9a-f]{64}\s*", created.stdout):
                    raise ServiceError("RUNNER_UNAVAILABLE", 503)
                process = subprocess.Popen(self._command() + ["start", "--attach", name],
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self._environment())
                output, diagnostics = bytearray(), bytearray()
                deadline = time.monotonic() + self.timeout_seconds
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ, output)
                    selector.register(process.stderr, selectors.EVENT_READ, diagnostics)
                    while selector.get_map():
                        if cancel_event is not None and cancel_event.is_set():
                            raise ServiceError("RUNNER_CANCELLED", 409)
                        if time.monotonic() >= deadline:
                            raise ServiceError("RUNNER_TIMEOUT", 422)
                        for key, _ in selector.select(timeout=min(0.1, max(0, deadline - time.monotonic()))):
                            block = os.read(key.fileobj.fileno(), 8192)
                            if not block:
                                selector.unregister(key.fileobj)
                                continue
                            key.data.extend(block)
                            if len(output) > MAX_OUTPUT or len(diagnostics) > MAX_STDERR:
                                raise ServiceError("RUNNER_OUTPUT_LIMIT", 422)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ServiceError("RUNNER_TIMEOUT", 422)
                process.wait(timeout=remaining)
                try:
                    result = json.loads(output)
                except (ValueError, UnicodeError):
                    raise ServiceError("RUNNER_RESULT_INVALID", 422) from None
                if process.returncode:
                    code = result.get("error", "RUNNER_FAILED") if isinstance(result, dict) else "RUNNER_FAILED"
                    if not isinstance(code, str) or not re.fullmatch(r"(?:PARSER|RUNNER)_[A-Z_]{1,60}", code):
                        code = "RUNNER_FAILED"
                    raise ServiceError(code, 422)
                return result
            except (OSError, subprocess.SubprocessError):
                raise ServiceError("RUNNER_UNAVAILABLE", 503) from None
            finally:
                try:
                    self._remove(name)
                finally:
                    if process:
                        if process.poll() is None:
                            process.kill()
                            process.wait(timeout=5)
                        process.stdout.close()
                        process.stderr.close()

    def status(self, *, _acquired=False):
        acquired_here = False
        try:
            if not _acquired:
                if not _JOBS.acquire(blocking=False):
                    raise ServiceError("RUNNER_BUSY", 429)
                acquired_here = True
            self._check_image()
            self._check_resource_support()
            if not self._checked:
                result = self._run(b"synthetic-enforcement-probe", probe="enforcement")
                if not isinstance(result, dict) or result.get("ready") is not True:
                    raise ServiceError("RUNNER_ENFORCEMENT_UNAVAILABLE", 503)
                self._enforcement = result["checks"]
                self._checked = True
            return {"ready": True, "code": "READY", "parser_version": PARSER_VERSION,
                    "image_id": self.image_id, "formats": ["csv", "ods", "pdf"]}
        except ServiceError as error:
            return {"ready": False, "code": error.code, "parser_version": PARSER_VERSION}
        finally:
            if acquired_here:
                _JOBS.release()

    def parse(self, data: bytes, filename: str, *, cancel_event=None):
        if not isinstance(data, bytes) or not 0 < len(data) <= MAX_INPUT:
            raise ServiceError("PARSER_INPUT_LIMIT", 413)
        if (not isinstance(filename, str) or len(filename) > 250 or not filename
                or filename != Path(filename).name or any(x in filename for x in ("\\", "\x00", "/"))):
            raise ServiceError("PARSER_FILENAME_REJECTED", 422)
        kind = Path(filename).suffix.lower().lstrip(".")
        if kind not in {"csv", "ods", "pdf"}:
            raise ServiceError("PARSER_FORMAT_UNSUPPORTED", 422)
        if not _JOBS.acquire(blocking=False):
            raise ServiceError("RUNNER_BUSY", 429)
        try:
            status = self.status(_acquired=True)
            if not status["ready"]:
                raise ServiceError(status["code"], 503)
            result = self._run(data, kind=kind, cancel_event=cancel_event)
            self._validate_result(result, kind)
            return result
        finally:
            _JOBS.release()

    @staticmethod
    def _validate_result(result, kind):
        if not isinstance(result, dict) or set(result) != {"parser_version", "format", "sheets"} or result["parser_version"] != PARSER_VERSION or result["format"] != kind:
            raise ServiceError("RUNNER_RESULT_INVALID", 422)
        sheets = result["sheets"]
        if not isinstance(sheets, list) or not 1 <= len(sheets) <= 256:
            raise ServiceError("RUNNER_RESULT_INVALID", 422)
        names, count = set(), 0
        for sheet in sheets:
            if not isinstance(sheet, dict) or set(sheet) != {"name", "rows"}:
                raise ServiceError("RUNNER_RESULT_INVALID", 422)
            name, rows = sheet["name"], sheet["rows"]
            if not isinstance(name, str) or not name or len(name) > 250 or name in names or not isinstance(rows, list):
                raise ServiceError("RUNNER_RESULT_INVALID", 422)
            names.add(name)
            previous = 0
            for row in rows:
                if not isinstance(row, dict) or set(row) != {"row_number", "cells"}:
                    raise ServiceError("RUNNER_RESULT_INVALID", 422)
                number, cells = row["row_number"], row["cells"]
                if type(number) is not int or not previous < number <= 1048576 or not isinstance(cells, list) or not 1 <= len(cells) <= 1024:
                    raise ServiceError("RUNNER_RESULT_INVALID", 422)
                if any(not isinstance(cell, str) or len(cell) > 16384 or "\x00" in cell for cell in cells):
                    raise ServiceError("RUNNER_RESULT_INVALID", 422)
                previous = number
                count += 1
                if count > 4096:
                    raise ServiceError("RUNNER_RESULT_INVALID", 422)
        if not count:
            raise ServiceError("RUNNER_RESULT_INVALID", 422)
