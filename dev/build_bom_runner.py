#!/usr/bin/env python3
"""Build and probe the fixed BOM Runner; save an immutable image ID outside Git.

The Docker build context contains only four public parser/build files. Original
BOM files, runtime settings, database contents and source manifests never enter it.
"""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from service.bom_runner import BomRunner, PARSER_VERSION, RUNNER_DIRECTORY, runner_source_hash
from service.errors import ServiceError
from dev.service_stack import local_docker_command, SELECTORS, StackError


def build(socket_path, output, ca_bundle=None):
    output = Path(output).expanduser()
    if output.is_symlink():
        raise ServiceError("RUNNER_OUTPUT_PATH_REJECTED", 422)
    output = output.resolve()
    if output.is_relative_to(ROOT) or not output.parent.is_dir():
        raise ServiceError("RUNNER_OUTPUT_MUST_BE_EXTERNAL", 422)
    if output.exists() and (not output.is_file() or output.stat().st_mode & 0o077):
        raise ServiceError("RUNNER_OUTPUT_REQUIRES_PRIVATE_PERMISSIONS", 422)
    environment = {k: v for k, v in os.environ.items() if k not in SELECTORS}
    docker = local_docker_command(socket_path)
    source_hash = runner_source_hash()
    with tempfile.TemporaryDirectory(prefix="ontologics-runner-build-") as temporary:
        directory = Path(temporary)
        context = directory / "context"
        context.mkdir()
        for name in ("Dockerfile", "requirements.lock", "parse.py", "probe.py"):
            shutil.copyfile(RUNNER_DIRECTORY / name, context / name)
        iidfile = directory / "image-id"
        command = docker + ["build", "--progress=plain", "--iidfile", str(iidfile),
                            "--build-arg", "RUNNER_SOURCE_HASH=" + source_hash]
        if ca_bundle:
            ca = Path(ca_bundle).expanduser().resolve(strict=True)
            if not ca.is_file() or "," in str(ca):
                raise ServiceError("RUNNER_CA_INVALID", 422)
            command += ["--secret", "id=proxy_ca,src=" + str(ca)]
        command += [str(context)]
        result = subprocess.run(command, env=environment, capture_output=True, timeout=300)
        if result.returncode:
            # Docker diagnostics can contain proxy information. Keep them out of
            # the service and expose only a fixed setup failure code.
            raise ServiceError("RUNNER_BUILD_FAILED", 503)
        image_id = iidfile.read_text().strip()
    runner = BomRunner(image_id=image_id, docker_socket=socket_path, timeout_seconds=30)
    status = runner.status()
    metadata = {"image_id": image_id, "source_sha256": source_hash,
                "parser_version": PARSER_VERSION, "enforcement": runner._enforcement,
                "ready": status["ready"], "code": status["code"],
                "scope": "local-development-fixed-parser", "formats": ["csv", "ods", "pdf"]}
    descriptor, temporary = tempfile.mkstemp(prefix="bom-runner-", suffix=".json", dir=output.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(metadata, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, output)
    finally:
        if Path(temporary).exists():
            Path(temporary).unlink()
    return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docker-socket", default="/var/run/docker.sock")
    parser.add_argument("--output", required=True)
    parser.add_argument("--ca-bundle", default=os.environ.get("CODEX_PROXY_CERT"))
    args = parser.parse_args()
    try:
        metadata = build(args.docker_socket, args.output, args.ca_bundle)
        print(json.dumps({"runner": "READY" if metadata["ready"] else "BLOCKED", "image_id": metadata["image_id"],
                          "parser_version": metadata["parser_version"], "code": metadata["code"],
                          "enforcement": "PASS" if metadata["ready"] else "NOT_READY"}))
        if not metadata["ready"]:
            raise SystemExit(1)
    except (ServiceError, StackError) as error:
        print(getattr(error, "code", str(error)))
        raise SystemExit(1) from None
    except (OSError, ValueError, subprocess.SubprocessError):
        print("RUNNER_BUILD_UNAVAILABLE")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
