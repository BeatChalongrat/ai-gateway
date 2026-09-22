#!/usr/bin/env python3
"""FIX128 C — generate docker-compose.test-stack.yml from the REAL stack.

The test stack is the real services, same images, same environment — so it
cannot drift from what runs — minus everything that would let it touch the
live system:

* its own compose project (``-p sran-test``) → its own network, so ``postgres``,
  ``redis``, ``backend``… resolve to the test containers only;
* its own named volumes (``ts_*``) — never the live data volumes;
* no bind mounts of the source tree or the Docker socket (images carry the
  code; no sandbox containers are spawned on the host);
* no ``container_name`` (the live stack owns ``ai-gateway-*``);
* loopback-only ports shifted into 1xxxx, none overlapping the live stack;
* only the services the destructive tests need (see SERVICES).

``docker compose config --no-interpolate`` keeps every ``${VAR}`` reference as
text, so the generated file holds no secret: values are read from ``.env`` when
the stack is brought up. Regenerate after a compose change:

    python scripts/test_stack_gen.py      # writes docker-compose.test-stack.yml
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ["docker-compose.yml", "docker-compose.override.yml", "docker-compose.webchat.yml"]

# service -> loopback host port (None = not published)
SERVICES: dict[str, int | None] = {
    "postgres": None,
    "redis": None,
    "mock-provider": 19090,
    "ner-service": None,
    "dlp-sidecar": None,
    "rate-limiter": None,
    "log-worker": None,
    "backend": 18081,
    "gateway": 18080,
    "postgres-webchat": 15433,
    "redis-webchat": 16380,
    "chat-edge-api": 14000,
}

# Services built from source in the real stack run the tip's images here. The
# tag is a variable so `scripts/test-stack.sh` can pin the live revision.
IMAGES = {
    "backend": "ai-gateway-backend:${TS_BACKEND_TAG:-latest}",
    "gateway": "ai-gateway-gateway:${TS_GATEWAY_TAG:-latest}",
    "chat-edge-api": "ai-gateway-chat-edge-api:${TS_CHAT_EDGE_TAG:-latest}",
}

# Bind mounts that are configuration, not source or host control — kept
# (read-only): the chat DB migrations the Postgres init applies.
KEEP_BIND_TARGETS = {"/docker-entrypoint-initdb.d", "/docker-entrypoint-initdb.d/02-chat-edge.sh", "/chat-edge-migrations"}

TEST_ENV = {
    # Nothing in the test stack may run a turn's code in a host container, and
    # nothing may call back into the live stack: the real chat-edge reaches the
    # gateway and the task authorizer through host.docker.internal (= the live
    # ports on this machine) — here they are the test stack's own services.
    "chat-edge-api": {"SANDBOX_ENABLED": "0", "CLOUD_SCHEDULER_ENABLED": "false",
                      "UNATTACHED_FILE_SWEEP_MODE": "off",
                      "GATEWAY_API_URL": "http://gateway:8080/v1",
                      "CLOUD_TASK_AUTHORIZE_URL": "http://backend:8080",
                      "CORS_ALLOW_ORIGINS": "http://localhost:13002"},
    "backend": {"APP_ENV": "development"},
}

# Env keys dropped outright (sandbox plumbing that names host paths).
DROP_ENV = {"chat-edge-api": {"SANDBOX_HOST_WORKDIR", "SANDBOX_SECCOMP_PATH", "SANDBOX_WORKDIR", "SANDBOX_RUNTIME", "SANDBOX_IMAGE"}}

# If any of these survive into the generated file, generation fails: each one
# is a way for a test-stack container to reach the live stack.
LIVE_REFERENCES = ["host.docker.internal", "localhost:8080", "localhost:8081", "localhost:4000",
                   "localhost:5433", "localhost:6380", "ai-gateway_", "ai-gateway-", "/var/run/docker.sock"]


def main() -> int:
    cmd = ["docker", "compose", "-p", "sran-test"]
    for f in SOURCES:
        cmd += ["-f", str(ROOT / f)]
    cmd += ["config", "--no-interpolate", "--format", "json"]
    merged = json.loads(subprocess.run(cmd, check=True, capture_output=True, text=True, cwd=ROOT).stdout)

    out_services: dict[str, dict] = {}
    for name, port in SERVICES.items():
        svc = dict(merged["services"][name])
        svc.pop("container_name", None)
        svc.pop("build", None)
        svc.pop("profiles", None)
        if name in IMAGES:
            svc["image"] = IMAGES[name]
        # ports: loopback only, shifted
        svc.pop("ports", None)
        if port is not None:
            target = {"backend": 8080, "gateway": 8080, "chat-edge-api": 4000, "mock-provider": 9090,
                      "postgres-webchat": 5432, "redis-webchat": 6379}[name]
            svc["ports"] = [f"127.0.0.1:{port}:{target}"]
        # volumes: own named volumes; keep only config binds (read-only)
        vols = []
        for v in svc.get("volumes", []) or []:
            if v.get("type") == "volume" and v.get("source") in ("postgres_data", "pgdata-webchat", "chat_edge_uploads"):
                vols.append({"type": "volume", "source": "ts_" + v["source"].replace("-", "_"), "target": v["target"]})
            elif v.get("type") == "bind" and v.get("target") in KEEP_BIND_TARGETS:
                vols.append({**v, "read_only": True})
        if vols:
            svc["volumes"] = vols
        else:
            svc.pop("volumes", None)
        # dependencies: only on services that exist here
        deps = svc.get("depends_on")
        if isinstance(deps, dict):
            svc["depends_on"] = {k: v for k, v in deps.items() if k in SERVICES}
            if not svc["depends_on"]:
                svc.pop("depends_on")
        # the dev backend command hot-reloads a bind mount that is not here
        if name == "backend":
            svc["command"] = ["sh", "-c", "python -m alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8080"]
        env = svc.get("environment") or {}
        if isinstance(env, list):
            env = dict(e.split("=", 1) if "=" in e else (e, None) for e in env)
        for k in DROP_ENV.get(name, ()):
            env.pop(k, None)
        env.update(TEST_ENV.get(name, {}))
        svc["environment"] = env
        svc["restart"] = "on-failure"  # first Postgres init restarts the server once
        svc.pop("extra_hosts", None)  # host.docker.internal = the live ports
        svc.setdefault("labels", {})
        if isinstance(svc["labels"], dict):
            svc["labels"]["sran.test-stack"] = "true"
        out_services[name] = svc

    doc = {
        "name": "sran-test",
        "services": out_services,
        "volumes": {"ts_postgres_data": {}, "ts_pgdata_webchat": {}, "ts_chat_edge_uploads": {}},
    }
    header = (
        "# GENERATED by scripts/test_stack_gen.py from the real compose files (FIX128 C).\n"
        "# Do not edit by hand; regenerate. Holds ${VAR} references only - no secrets.\n"
        "# Up/down: scripts/test-stack.sh up|down|reset|status   (project: sran-test)\n"
    )
    text = header + json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
    # Scan what can reach out: env values, extra_hosts, volume/network sources.
    # (Healthchecks talk to localhost INSIDE their own container; images are
    # images.)
    reach = json.dumps([{k: s.get(k) for k in ("environment", "extra_hosts", "volumes", "networks", "container_name")}
                        for s in out_services.values()])
    leaks = [m for m in LIVE_REFERENCES if m in reach]
    if leaks:
        sys.stderr.write(f"REFUSED: the test stack would reference the live stack: {leaks}\n")
        return 1
    out = ROOT / "docker-compose.test-stack.yml"
    out.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {out.name}: {len(out_services)} services")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
