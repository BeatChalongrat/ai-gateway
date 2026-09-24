"""FIX131 — the backend's production process: gunicorn + N uvicorn workers.

This file is the image's process model (``backend/serve.sh`` runs
``gunicorn -c gunicorn.conf.py app.main:app``). The dev hot-reload shape
(``uvicorn --reload`` on a bind mount) is opt-in: docker-compose.dev.yml.

Knobs (environment, read when the master starts):

WORKERS / GUNICORN_WORKERS worker processes. Empty = computed from the
                           machine (app/core/sizing.py: cores, RAM, the Postgres
                           connection budget) — FIX133 A1. Sizing, measured on
                           the live box (12 vCPU / 16 GB Docker VM, shared with
                           NER, two Postgres, chat-edge, gateway, three Next.js
                           dev servers): NIGHT3 saw ONE process saturate at
                           130-182 % CPU with 10 users (event-loop lag 5->44 ms,
                           first-token p95 15 s) and the same curve on the mock
                           provider, so the process was the bottleneck. 4
                           workers give ~3x that headroom while leaving 8 vCPU
                           to the co-tenants, and keep the Postgres budget
                           (35 connections per worker x 4 = 140 of 250). More
                           workers = more connections; see
                           docs/ops/deploy-backend.md before raising it.
GUNICORN_TIMEOUT           worker heartbeat, seconds (default 60). Uvicorn
                           workers heartbeat from their event loop every
                           timeout/2 s, NOT per request, so a 15-minute stream
                           never trips it; only an event loop blocked for the
                           whole timeout does (the watchdog we want). The
                           master's SIGTERM path (docker stop) does not apply
                           it at all — graceful_timeout bounds that. It WOULD
                           cut a stream during a worker-level recycle (HUP,
                           max_requests), which is why max_requests stays 0.
GUNICORN_GRACEFUL_TIMEOUT  how long in-flight requests get to finish after
                           SIGTERM, seconds (default 180): hidden thinking can
                           run ~2 min before the answer streams, and chat-edge
                           keeps a turn alive as long as frames (or the 10 s
                           thinking keepalive) flow. docker-compose.yml's
                           stop_grace_period (200 s) must stay above it, or
                           Docker SIGKILLs the drain.
GUNICORN_KEEPALIVE         idle keep-alive seconds (default 5, what uvicorn
                           used standalone; gunicorn's own default is 2).
SRAN_RUNTIME_DIR           per-run state shared by the workers of THIS
                           container (default /tmp/sran-backend, wiped at every
                           start): prometheus/ (multiprocess metrics), workers/
                           (the worker board /ready reads) and the ``draining``
                           marker. Must match backend/healthcheck.py.
"""

from __future__ import annotations

import os
import shutil

RUNTIME_DIR_DEFAULT = "/tmp/sran-backend"


def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


runtime_dir = os.environ.get("SRAN_RUNTIME_DIR") or RUNTIME_DIR_DEFAULT
PROMETHEUS_DIR = os.path.join(runtime_dir, "prometheus")
WORKERS_DIR = os.path.join(runtime_dir, "workers")
DRAINING_MARKER = os.path.join(runtime_dir, "draining")

# Set in the MASTER before any worker forks, so every worker imports
# prometheus_client in multiprocess mode and finds the worker board. They are
# deliberately not container env (docker-compose.yml): a one-off
# `docker exec backend python ...` stays a plain single-process interpreter.
os.environ["PROMETHEUS_MULTIPROC_DIR"] = PROMETHEUS_DIR
os.environ["SRAN_WORKER_STATE_DIR"] = WORKERS_DIR

bind = "0.0.0.0:8080"
worker_class = "uvicorn.workers.UvicornWorker"


def _workers() -> int:
    """FIX133 A1 — WORKERS (or GUNICORN_WORKERS) from .env; empty = computed.

    The formula lives in app/core/sizing.py: min(cpus // 3, RAM / 250 MB,
    180 Postgres connections / 35 per worker), at least 2 — 4 on the live
    12-vCPU box, where FIX131 measured 4 and 6 workers within noise.
    """
    for name in ("WORKERS", "GUNICORN_WORKERS"):
        v = _env_int(name, 0)
        if v > 0:
            return v
    try:
        import sys as _sys

        _here = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else os.getcwd()
        if _here not in _sys.path:
            _sys.path.insert(0, _here)
        from app.core.sizing import default_backend_workers

        return default_backend_workers()
    except Exception:  # noqa: BLE001 — a sizing error must never stop the master
        return 4


workers = max(1, _workers())
timeout = _env_int("GUNICORN_TIMEOUT", 60)
graceful_timeout = _env_int("GUNICORN_GRACEFUL_TIMEOUT", 180)
keepalive = _env_int("GUNICORN_KEEPALIVE", 5)
# Recycling a worker drains it WITHOUT heartbeats (see GUNICORN_TIMEOUT) and
# interrupted SSE streams in waves in the capacity runs
# (docs/CAPACITY_VALIDATION_150_250.md). Off.
max_requests = 0
# The heartbeat file on tmpfs: a heartbeat on the container's overlay disk can
# stall behind disk I/O and get a healthy worker killed (gunicorn docs, "Blocking
# OS calls").
worker_tmp_dir = "/dev/shm"
# No preload: each worker imports the app itself, so a worker that dies takes
# nothing shared with it, and no DB connection is ever created before fork.
preload_app = False
# The app logs every request itself (RequestTimingMiddleware); uvicorn's
# access line on top of it was pure volume. Errors still go to stderr.
accesslog = None
errorlog = "-"
loglevel = os.environ.get("GUNICORN_LOG_LEVEL", "info")
# gunicorn >= 25 opens a control socket under $HOME by default; nothing here
# uses it, and a root-owned socket in the image is one more thing to explain.
control_socket_disable = True


def on_starting(server) -> None:
    """Master, before the first worker: a clean runtime dir for this run.

    prometheus_client requires the multiprocess directory to be emptied
    between runs (stale files from a previous run would be summed in), and a
    ``draining`` marker left by the previous run must not make the new one
    look healthy while it is still booting.
    """
    shutil.rmtree(runtime_dir, ignore_errors=True)
    for d in (PROMETHEUS_DIR, WORKERS_DIR):
        os.makedirs(d, exist_ok=True)


def when_ready(server) -> None:
    """Master: mark the container as draining the moment SIGTERM arrives.

    From that moment the listening socket is closed and /ready can no longer
    be reached, so the container healthcheck would fail and autoheal would
    RESTART a container that is shutting down on purpose — cutting the very
    drain this config exists for. backend/healthcheck.py reports healthy while
    the marker exists (the Kubernetes rule: a terminating pod is not killed
    for failing its probe). gunicorn dispatches signals as
    ``getattr(arbiter, "handle_" + name)``; the instance attribute below runs
    first, then the stock handler.
    """
    stock = server.handle_term

    def handle_term() -> None:
        mark_draining(server)
        stock()

    server.handle_term = handle_term


def mark_draining(server=None) -> None:
    try:
        os.makedirs(runtime_dir, exist_ok=True)
        with open(DRAINING_MARKER, "w", encoding="utf-8") as fh:
            fh.write(str(os.getpid()))
        if server is not None:
            server.log.info(
                "SIGTERM: draining in-flight requests for up to %ss", graceful_timeout
            )
    except OSError:
        pass


def child_exit(server, worker) -> None:
    """Master, after a worker exited: forget its live gauges and its board entry."""
    try:
        from prometheus_client import multiprocess

        multiprocess.mark_process_dead(worker.pid, PROMETHEUS_DIR)
    except Exception:  # noqa: BLE001 — metrics hygiene must never stop the master
        pass
    try:
        os.remove(os.path.join(WORKERS_DIR, f"{worker.pid}.json"))
    except OSError:
        pass
