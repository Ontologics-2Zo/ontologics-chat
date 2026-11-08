"""Fixed synthetic enforcement probes; no original documents or host secrets."""

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time


def cgroup(name):
    return Path("/sys/fs/cgroup", name).read_text().strip()


def writable(path):
    try:
        with open(path, "ab"):
            pass
        return True
    except OSError:
        return False


def connectable(address):
    try:
        with socket.create_connection(address, timeout=0.25):
            return True
    except OSError:
        return False


def basic():
    memory = cgroup("memory.max")
    cpu = cgroup("cpu.max").split()
    pids = cgroup("pids.max")
    status = Path("/proc/self/status").read_text()
    return {"nonroot": os.getuid() == 65532,
            "readonly_root": not writable("/opt/runner/forbidden"),
            "readonly_input": not writable("/input/blob"),
            "no_docker_socket": not Path("/var/run/docker.sock").exists(),
            "no_host_secrets": not any(os.access(p, os.R_OK) for p in ("/root/.aws", "/root/.ssh", "/workspace", "/home/agent")),
            "no_network": not connectable(("1.1.1.1", 443)) and not connectable(("127.0.0.1", 55432)),
            "no_proxy": all(not os.environ.get(k) for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")),
            "memory_limit": memory != "max" and 0 < int(memory) <= 268435456,
            "pids_limit": pids != "max" and 0 < int(pids) <= 32,
            "cpu_limit": cpu[0] != "max" and int(cpu[0]) / int(cpu[1]) <= 0.5,
            "no_new_privileges": "NoNewPrivs:\t1" in status,
            "seccomp_filter": "Seccomp:\t2" in status,
            "capabilities_dropped": "CapEff:\t0000000000000000" in status}


def enforcement():
    results = basic()
    if not all(results.values()):
        return results
    # The child deliberately exceeds its cgroup. The surrounding process is
    # small; the kernel should kill the child allocation, never a host process.
    child = subprocess.run([sys.executable, "-I", "-c", "x=bytearray(400*1024*1024);print(len(x))"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    results["memory_enforced"] = child.returncode == -9 and "oom_kill 0" not in cgroup("memory.events")
    children = []
    refused = False
    try:
        for _ in range(40):
            try:
                children.append(subprocess.Popen([sys.executable, "-I", "-c", "import time;time.sleep(20)"],
                                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            except OSError:
                refused = True
                break
        results["pids_enforced"] = refused and 0 < len(children) < 32
    finally:
        for child in children:
            child.kill()
        for child in children:
            child.wait()
    before = cgroup("cpu.stat")
    subprocess.run([sys.executable, "-I", "-c", "import time;e=time.monotonic()+2\nwhile time.monotonic()<e: pass"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=8)
    stat = dict(line.split() for line in cgroup("cpu.stat").splitlines())
    prior = dict(line.split() for line in before.splitlines())
    results["cpu_enforced"] = int(stat.get("nr_throttled", 0)) > int(prior.get("nr_throttled", 0))
    return results


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) == 2 else "basic"
    if mode == "timeout":
        subprocess.Popen([sys.executable, "-I", "-c", "import time;time.sleep(120)"])
        time.sleep(120)
    elif mode == "output":
        sys.stdout.write("x" * (3 * 1024 * 1024))
    elif mode in {"basic", "enforcement"}:
        try:
            result = basic() if mode == "basic" else enforcement()
            value = {"checks": result, "ready": all(result.values())}
            if not value["ready"]:
                value["error"] = "RUNNER_ENFORCEMENT_UNAVAILABLE"
            print(json.dumps(value, separators=(",", ":")))
            raise SystemExit(0 if all(result.values()) else 2)
        except (OSError, ValueError, subprocess.SubprocessError):
            print('{"ready":false,"error":"RUNNER_ENFORCEMENT_UNAVAILABLE"}')
            raise SystemExit(2)
    else:
        raise SystemExit(2)
