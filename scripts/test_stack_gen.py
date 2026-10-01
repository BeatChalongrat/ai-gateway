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
* only the services the destructive tests need (see SERVICES);
* none of the OWNER's destinations (FIX130): the alert channels
  (``ALERT_*``, ``RESEND_API_KEY``) and the off-site backup repository
  (``BACKUP_OFFSITE_*``) are replaced by their ``TS_`` twins — empty unless
  set, so a test-stack alert is logged only and a test-stack backup stays
  local. Before FIX130 the test stack's backend and backup service read
  the live ``ALERT_NTFY_URL`` and paged the owner.

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
    # FIX129 A5: the backup service, so a restore drill can run against the
    # test stack (scripts/probes/FIX129/backup_drill_check.sh). Its /backups
    # bind becomes the named volume ts_backups (see below). NOT included:
    # autoheal (needs the docker socket = a live reference).
    "backup": None,
    # FIX132 B: the knowledge base's models, internal only (no port). Reads
    # the live weights volume READ-ONLY (see SHARED_READ_ONLY_VOLUMES).
    "kb-models": None,
}

# FIX132 B — the ONE live volume the test stack may mount: kb-models' pinned
# model weights (~4.6 GB of files that never change at runtime, not data), so
# the test stack does not download them again. Mounted read-only, declared
# `external` (so `test-stack.sh reset` — down --volumes — can never delete
# it), and kb-models runs with HF_HUB_OFFLINE=1. compose volume -> (test-stack
# volume key, live volume name).
SHARED_READ_ONLY_VOLUMES = {"kb_models": ("ts_kb_models_live_ro", "ai-gateway_kb_models")}

# Services built from source in the real stack run the tip's images here. The
# tag is a variable so `scripts/test-stack.sh` can pin the live revision.
IMAGES = {
    "backend": "ai-gateway-backend:${TS_BACKEND_TAG:-latest}",
    "gateway": "ai-gateway-gateway:${TS_GATEWAY_TAG:-latest}",
    "chat-edge-api": "ai-gateway-chat-edge-api:${TS_CHAT_EDGE_TAG:-latest}",
    "kb-models": "ai-gateway-kb-models:${TS_KB_MODELS_TAG:-latest}",
}

# Bind mounts that are configuration, not source or host control — kept
# (read-only): the chat DB migrations the Postgres init applies.
KEEP_BIND_TARGETS = {"/docker-entrypoint-initdb.d", "/docker-entrypoint-initdb.d/02-chat-edge.sh", "/chat-edge-migrations"}

# AM7 — services the test stack has that the live stack does not.
#
# mail-sink: a local SMTP sink (Mailpit). Agent Market's mail relay is set to
# it on the admin page — host mail-sink, port 1025, no encryption, no user —
# so an approved message, the admin's test message and a reminder are
# composed, sent over SMTP and received for real; only the relay is
# simulated. What arrived: http://127.0.0.1:18025 (UI and /api/v1/messages).
# It is the only SMTP server reachable from here: the owner's relay password
# is redirected (TS_AGENT_MARKET_SMTP_PASSWORD, empty).
EXTRA_SERVICES = {
    "mail-sink": {
        "image": "axllent/mailpit:v1.27",
        "environment": {"MP_MAX_MESSAGES": "500", "MP_SMTP_DISABLE_RDNS": "true"},
        "ports": ["127.0.0.1:18025:8025", "127.0.0.1:11025:1025"],
    },
}

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
    # FIX131 D4 — every alert from the test stack says [TEST] in title and
    # body and goes only to ALERT_TEST_NTFY_URL (= TS_ALERT_TEST_NTFY_URL, a
    # throwaway topic; empty = logged only) — never an owner channel, even
    # if a TS_ALERT_NTFY_URL were set to the owner's topic by mistake.
    # The first admin of the test stack is its own (never the address or password
    # of the live deployment, which the root .env carries): the dev password applies.
    "backend": {"APP_ENV": "development", "ALERT_ENVIRONMENT": "test",
                "INITIAL_ADMIN_EMAIL": "ts-admin@fix128.test", "INITIAL_ADMIN_USERNAME": "admin",
                "INITIAL_ADMIN_PASSWORD": ""},
    "backup": {"ALERT_ENVIRONMENT": "test"},
    # FIX132 B: the weights are mounted read-only; never try to download.
    "kb-models": {"HF_HUB_OFFLINE": "1"},
}

# Env keys dropped outright (sandbox plumbing that names host paths).
DROP_ENV = {"chat-edge-api": {"SANDBOX_HOST_WORKDIR", "SANDBOX_SECCOMP_PATH", "SANDBOX_WORKDIR", "SANDBOX_RUNTIME", "SANDBOX_IMAGE"}}

# FIX130 — env keys that name the OWNER's destinations and credentials, in
# any service: alert channels (the phone, the inbox, a webhook, the Resend
# key that sends as the owner) and the off-site backup repository + its keys
# (a test backup must never upload into, or prune by retention, the owner's
# bucket). Each value becomes its TS_ twin: "${ALERT_NTFY_URL:-}" ->
# "${TS_ALERT_NTFY_URL:-}" (default kept), a literal -> "${TS_<KEY>:-}".
# Unset TS_ variables = log only / off-site not configured.
# AM7: AGENT_MARKET_SMTP_PASSWORD is the customer's mail relay password — the
# test stack's Agent Market sends to the local sink below, never the relay.
REDIRECT_ENV_KEYS = {"RESEND_API_KEY", "AGENT_MARKET_SMTP_PASSWORD"}
REDIRECT_ENV_PREFIXES = ("ALERT_", "BACKUP_OFFSITE_")

# If any of these survive into the generated file, generation fails: each one
# is a way for a test-stack container to reach the live stack — or the owner
# (a live alert/off-site variable, or a public ntfy topic written literally).
LIVE_REFERENCES = ["host.docker.internal", "localhost:8080", "localhost:8081", "localhost:4000",
                   "localhost:5433", "localhost:6380", "ai-gateway_", "ai-gateway-", "/var/run/docker.sock",
                   "${ALERT_", "${RESEND_API_KEY", "${BACKUP_OFFSITE_", "ntfy.sh/", "${AGENT_MARKET_SMTP_PASSWORD"]


def redirect_env(key: str, value):
    """The test-stack value of an owner-destination variable (see above)."""
    if isinstance(value, str) and value.startswith("${" + key):
        return "${TS_" + value[2:]
    return "${TS_" + key + ":-}"


def _is_redirected(key: str) -> bool:
    return key in REDIRECT_ENV_KEYS or key.startswith(REDIRECT_ENV_PREFIXES)


def shared_volumes(services: dict) -> dict:
    """The top-level declarations of the live volumes the services mount
    (SHARED_READ_ONLY_VOLUMES): external, by their live name. Raises
    ValueError if any mount of one is not read-only."""
    out = {}
    for key, live in SHARED_READ_ONLY_VOLUMES.values():
        mounts = [v for s in services.values() for v in (s.get("volumes") or []) if v.get("source") == key]
        if not mounts:
            continue
        if any(not v.get("read_only") for v in mounts):
            raise ValueError(f"the live volume {live} must be mounted read-only")
        out[key] = {"external": True, "name": live}
    return out


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
            if v.get("type") == "volume" and v.get("source") in ("postgres_data", "pgdata-webchat", "chat_edge_uploads",
                                                                 "backend_uploads"):
                vols.append({"type": "volume", "source": "ts_" + v["source"].replace("-", "_"),
                             "target": v["target"], **({"read_only": True} if v.get("read_only") else {})})
            elif v.get("type") == "volume" and v.get("source") in SHARED_READ_ONLY_VOLUMES:
                vols.append({"type": "volume", "source": SHARED_READ_ONLY_VOLUMES[v["source"]][0],
                             "target": v["target"], "read_only": True})
            elif v.get("type") == "bind" and v.get("target") in KEEP_BIND_TARGETS:
                vols.append({**v, "read_only": True})
            elif name == "backup" and v.get("target") == "/backups":
                # the host backup directory is a live path; the test stack backs up into its own volume
                vols.append({"type": "volume", "source": "ts_backups", "target": "/backups"})
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
        # FIX131: no command override — the backend runs the image's own
        # process (serve.sh: migrate, then gunicorn with GUNICORN_WORKERS
        # workers), the same one live runs, so a load test here measures it.
        env = svc.get("environment") or {}
        if isinstance(env, list):
            env = dict(e.split("=", 1) if "=" in e else (e, None) for e in env)
        for k in DROP_ENV.get(name, ()):
            env.pop(k, None)
        for k in [k for k in env if _is_redirected(k)]:
            env[k] = redirect_env(k, env[k])
        env.update(TEST_ENV.get(name, {}))
        svc["environment"] = env
        svc["restart"] = "on-failure"  # first Postgres init restarts the server once
        svc.pop("extra_hosts", None)  # host.docker.internal = the live ports
        svc.setdefault("labels", {})
        if isinstance(svc["labels"], dict):
            svc["labels"]["sran.test-stack"] = "true"
        out_services[name] = svc

    for name, extra in EXTRA_SERVICES.items():
        out_services[name] = {**extra, "restart": "on-failure", "labels": {"sran.test-stack": "true"}}

    volumes = {"ts_postgres_data": {}, "ts_pgdata_webchat": {}, "ts_chat_edge_uploads": {}, "ts_backups": {},
               "ts_backend_uploads": {}}
    try:
        volumes.update(shared_volumes(out_services))
    except ValueError as exc:
        sys.stderr.write(f"REFUSED: {exc}\n")
        return 1
    doc = {
        "name": "sran-test",
        "services": out_services,
        "volumes": volumes,
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
    # A kept bind's source is by construction a file of THIS checkout (see
    # KEEP_BIND_TARGETS); it is scanned as a path relative to the checkout so
    # the guard does not depend on the directory name (a worktree called
    # ai-gateway-<card> would otherwise trip "ai-gateway-"). A bind to
    # anything outside the checkout (the docker socket, a host directory)
    # still carries its full path and still trips.
    def _scan_view(s: dict) -> dict:
        view = {k: s.get(k) for k in ("environment", "extra_hosts", "volumes", "networks", "container_name")}
        vols = []
        for v in view.get("volumes") or []:
            src = str(v.get("source", ""))
            if v.get("type") == "bind" and Path(src).is_absolute() and Path(src).resolve().is_relative_to(ROOT):
                v = {**v, "source": "./" + Path(src).resolve().relative_to(ROOT).as_posix()}
            vols.append(v)
        view["volumes"] = vols
        return view
    reach = json.dumps([_scan_view(s) for s in out_services.values()])
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
