#!/usr/bin/env python3
"""Preserving local PostgreSQL/Neo4j lifecycle; credentials stay outside Git.

Only ``up`` and ``status`` are supported. Existing containers are reused only
after their images, loopback ports, persistent volumes and credentials match.
This utility does not stop, reset, delete or migrate a database.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import tempfile
import time
from urllib.parse import quote, unquote, urlsplit


REPO_ROOT = Path(__file__).resolve().parents[1]
DOCKER = ["docker", "--host=unix:///var/run/docker.sock"]
SELECTORS = ("DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_TLS", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH")


class StackError(Exception):
    """Fixed public-safe code; Docker diagnostics may contain credentials."""


def docker(*args, allow_missing=False, timeout=60):
    environment = {k: v for k, v in os.environ.items() if k not in SELECTORS}
    try:
        result = subprocess.run(DOCKER + list(args), env=environment, capture_output=True,
                                text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise StackError("LOCAL_DOCKER_UNAVAILABLE") from None
    if result.returncode:
        if allow_missing and "no such" in result.stderr.lower():
            return None
        raise StackError("LOCAL_DOCKER_OPERATION_FAILED")
    return result.stdout


def runtime_path(value):
    path = Path(value).expanduser()
    if path.is_symlink():
        raise StackError("RUNTIME_FILE_SYMLINK_REJECTED")
    path = path.resolve()
    if path.is_relative_to(REPO_ROOT):
        raise StackError("RUNTIME_FILE_MUST_BE_OUTSIDE_REPOSITORY")
    if not path.parent.is_dir():
        raise StackError("RUNTIME_DIRECTORY_MISSING")
    if path.exists() and (not path.is_file() or stat.S_IMODE(path.stat().st_mode) & 0o077):
        raise StackError("RUNTIME_FILE_REQUIRES_PRIVATE_PERMISSIONS")
    return path


def load_runtime(path, ports):
    try:
        if path.stat().st_size > 16384:
            raise StackError("RUNTIME_CONFIGURATION_INVALID")
        config = json.loads(path.read_text())
        parsed = urlsplit(config["DATABASE_URL"])
        if (parsed.scheme != "postgresql" or parsed.hostname != "127.0.0.1" or parsed.port != ports[0]
                or not parsed.username or not parsed.password or not parsed.path.strip("/")
                or parsed.query or parsed.fragment):
            raise StackError("RUNTIME_CONFIGURATION_INVALID")
        if not isinstance(config["NEO4J_PASSWORD"], str) or len(config["NEO4J_PASSWORD"]) < 8:
            raise StackError("RUNTIME_CONFIGURATION_INVALID")
        for value in (unquote(parsed.username), unquote(parsed.password), parsed.path[1:], config["NEO4J_PASSWORD"]):
            if any(char in value for char in "\r\n\x00"):
                raise StackError("RUNTIME_CONFIGURATION_INVALID")
        if config.get("NEO4J_URI", f"bolt://127.0.0.1:{ports[2]}") != f"bolt://127.0.0.1:{ports[2]}":
            raise StackError("RUNTIME_CONFIGURATION_INVALID")
        return config
    except (OSError, ValueError, KeyError, TypeError):
        raise StackError("RUNTIME_CONFIGURATION_INVALID") from None


def new_runtime(path, ports):
    password = secrets.token_urlsafe(36)
    config = {
        "DATABASE_URL": f"postgresql://ontologics:{quote(password, safe='')}@127.0.0.1:{ports[0]}/ontologics",
        "NEO4J_URI": f"bolt://127.0.0.1:{ports[2]}",
        "NEO4J_PASSWORD": secrets.token_urlsafe(36),
    }
    # O_EXCL protects existing configuration from replacement, including races.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(config, stream)
        stream.write("\n")
    return config


def specs(name, config, ports):
    pg = urlsplit(config["DATABASE_URL"])
    return [
        {"name": name + "-pg", "image": "postgres:17",
         "ports": {"5432/tcp": ports[0]}, "volume": name + "-pg-data",
         "destination": "/var/lib/postgresql/data",
         "environment": {"POSTGRES_USER": unquote(pg.username), "POSTGRES_PASSWORD": unquote(pg.password),
                         "POSTGRES_DB": unquote(pg.path[1:])}},
        {"name": name + "-neo4j", "image": "neo4j:5.26-community",
         "ports": {"7474/tcp": ports[1], "7687/tcp": ports[2]},
         "volume": name + "-neo4j-data", "destination": "/data",
         "environment": {"NEO4J_AUTH": "neo4j/" + config["NEO4J_PASSWORD"],
                         "NEO4J_server_memory_heap_initial__size": "256m",
                         "NEO4J_server_memory_heap_max__size": "512m",
                         "NEO4J_server_memory_pagecache_size": "256m"}},
    ]


def inspect_container(name):
    raw = docker("container", "inspect", name, allow_missing=True)
    return json.loads(raw)[0] if raw is not None else None


def verify_container(spec, actual):
    if actual["Config"]["Image"] != spec["image"]:
        raise StackError("EXISTING_CONTAINER_IMAGE_MISMATCH")
    ports = actual["HostConfig"]["PortBindings"]
    expected = {key: [{"HostIp": "127.0.0.1", "HostPort": str(value)}] for key, value in spec["ports"].items()}
    if ports != expected or actual["HostConfig"]["NetworkMode"] == "host":
        raise StackError("EXISTING_CONTAINER_BINDING_MISMATCH")
    mounts = [m for m in actual["Mounts"] if m["Destination"] == spec["destination"]]
    if len(mounts) != 1 or mounts[0]["Type"] != "volume" or mounts[0].get("Name") != spec["volume"]:
        raise StackError("EXISTING_CONTAINER_VOLUME_MISMATCH")
    environment = dict(entry.split("=", 1) for entry in actual["Config"].get("Env", []) if "=" in entry)
    sensitive_keys = ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB") if spec["name"].endswith("-pg") else ("NEO4J_AUTH",)
    if any(environment.get(key) != spec["environment"][key] for key in sensitive_keys):
        raise StackError("EXISTING_CONTAINER_CREDENTIAL_MISMATCH")


def create_container(spec, private_directory):
    # The command line contains only a private env-file path, never secret values.
    with tempfile.TemporaryDirectory(prefix="ontologics-stack-", dir=private_directory) as directory:
        environment_file = Path(directory) / "container.env"
        fd = os.open(environment_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write("\n".join(f"{key}={value}" for key, value in spec["environment"].items()) + "\n")
        args = ["run", "-d", "--name", spec["name"], "--env-file", str(environment_file),
                "--mount", f"type=volume,source={spec['volume']},target={spec['destination']}"]
        for container_port, host_port in spec["ports"].items():
            args.extend(["-p", f"127.0.0.1:{host_port}:{container_port.split('/')[0]}"])
        if spec["name"].endswith("-neo4j"):
            args.extend(["--mount", f"type=volume,source={spec['name']}-logs,target=/logs"])
        docker(*args, spec["image"], timeout=180)


def readiness(config, ports):
    try:
        import psycopg
        from neo4j import GraphDatabase
    except ImportError:
        raise StackError("SERVICE_DEPENDENCIES_REQUIRED") from None
    state = {"postgresql": False, "neo4j": False}
    try:
        with psycopg.connect(config["DATABASE_URL"], connect_timeout=2) as conn:
            state["postgresql"] = conn.execute("SELECT 1").fetchone() == (1,)
    except Exception:
        pass
    try:
        with GraphDatabase.driver(f"bolt://127.0.0.1:{ports[2]}", auth=("neo4j", config["NEO4J_PASSWORD"]),
                                  connection_timeout=2, connection_acquisition_timeout=2) as driver:
            driver.verify_connectivity()
            state["neo4j"] = True
    except Exception:
        pass
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("up", "status"))
    parser.add_argument("--runtime-file", required=True, help="Private mode-0600 JSON outside the repository")
    parser.add_argument("--name", default="ontologics")
    parser.add_argument("--postgres-port", type=int, default=55432)
    parser.add_argument("--neo4j-http-port", type=int, default=57474)
    parser.add_argument("--neo4j-bolt-port", type=int, default=57687)
    args = parser.parse_args()
    ports = (args.postgres_port, args.neo4j_http_port, args.neo4j_bolt_port)
    try:
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,39}", args.name) or len(set(ports)) != 3 or not all(1024 <= p <= 65535 for p in ports):
            raise StackError("STACK_ARGUMENT_INVALID")
        path = runtime_path(args.runtime_file)
        docker("info", "--format", "{{.ServerVersion}}")
        actual = {name: inspect_container(name) for name in (args.name + "-pg", args.name + "-neo4j")}
        if not path.exists():
            if args.command == "status":
                raise StackError("RUNTIME_CONFIGURATION_MISSING")
            if any(actual.values()):
                raise StackError("EXISTING_CONTAINER_REQUIRES_ORIGINAL_RUNTIME_FILE")
            for name in (args.name + "-pg-data", args.name + "-neo4j-data", args.name + "-neo4j-logs"):
                if docker("volume", "inspect", name, allow_missing=True) is not None:
                    raise StackError("EXISTING_VOLUME_REQUIRES_ORIGINAL_RUNTIME_FILE")
            config = new_runtime(path, ports)
        else:
            config = load_runtime(path, ports)
        stack = specs(args.name, config, ports)
        # Validate the entire existing stack before any start/create operation.
        for spec in stack:
            if actual[spec["name"]]:
                verify_container(spec, actual[spec["name"]])
        if args.command == "up":
            for spec in stack:
                container = actual[spec["name"]]
                if container is None:
                    create_container(spec, path.parent)
                elif not container["State"]["Running"]:
                    docker("start", spec["name"])
            deadline = time.monotonic() + 45
            while True:
                state = readiness(config, ports)
                if all(state.values()) or time.monotonic() >= deadline:
                    break
                time.sleep(0.5)
        else:
            state = readiness(config, ports)
        print(json.dumps({"stack": args.name, "services": state, "credentials": "external_private_file",
                          "database_reset": False}))
        return 0 if all(state.values()) else 1
    except (StackError, OSError, ValueError, KeyError) as error:
        code = str(error) if isinstance(error, StackError) else "STACK_CONFIGURATION_ERROR"
        print(json.dumps({"error": code}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
