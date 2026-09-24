"""FIX131 — the backend's production process model, pinned.

Before FIX131 the live backend was `uvicorn --reload` (one process, on a bind
mount of the checkout) and the image CMD was `sh -c "alembic && gunicorn ..."`
— gunicorn a child of a PID-1 shell that ignores SIGTERM, a 30 s drain, and
no stop_grace_period (this Docker engine's default is 1 s: in-flight turns
were SIGKILLed on every restart). These tests pin the replacement:
serve.sh execs gunicorn with gunicorn.conf.py; the drain outlasts a long
turn; Docker waits longer than the drain; the healthcheck does not call a
draining container unhealthy; a worker's metrics/board files go with it.
"""

from __future__ import annotations

import os
import re
import runpy
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
CONF = BACKEND / "gunicorn.conf.py"
COMPOSE = next((p for p in (BACKEND.parent / "docker-compose.yml", Path("/docker-compose.yml")) if p.is_file()), None)


def _load(monkeypatch, tmp_path, **env) -> dict:
    for k in ("GUNICORN_WORKERS", "GUNICORN_TIMEOUT", "GUNICORN_GRACEFUL_TIMEOUT", "GUNICORN_KEEPALIVE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SRAN_RUNTIME_DIR", str(tmp_path / "rt"))
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    # gunicorn.conf.py exports these into os.environ; keep the test process clean.
    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", "")
    monkeypatch.setenv("SRAN_WORKER_STATE_DIR", "")
    return runpy.run_path(str(CONF))


def test_defaults_are_the_production_shape(monkeypatch, tmp_path):
    cfg = _load(monkeypatch, tmp_path)
    assert cfg["worker_class"] == "uvicorn.workers.UvicornWorker"
    assert cfg["workers"] == 4
    assert cfg["timeout"] == 60
    assert cfg["graceful_timeout"] == 180          # ~2 min hidden thinking + the answer
    assert cfg["max_requests"] == 0                # recycling drains without heartbeats
    assert cfg["preload_app"] is False
    assert cfg["bind"] == "0.0.0.0:8080"
    assert cfg["worker_tmp_dir"] == "/dev/shm"
    rt = str(tmp_path / "rt")
    assert os.environ["PROMETHEUS_MULTIPROC_DIR"] == os.path.join(rt, "prometheus")
    assert os.environ["SRAN_WORKER_STATE_DIR"] == os.path.join(rt, "workers")


def test_env_knobs_are_honoured(monkeypatch, tmp_path):
    cfg = _load(monkeypatch, tmp_path, GUNICORN_WORKERS="6", GUNICORN_TIMEOUT="90",
                GUNICORN_GRACEFUL_TIMEOUT="240", GUNICORN_KEEPALIVE="7")
    assert (cfg["workers"], cfg["timeout"], cfg["graceful_timeout"], cfg["keepalive"]) == (6, 90, 240, 7)
    bad = _load(monkeypatch, tmp_path, GUNICORN_WORKERS="lots", GUNICORN_GRACEFUL_TIMEOUT="")
    assert bad["workers"] == 4 and bad["graceful_timeout"] == 180


def test_on_starting_gives_each_run_a_clean_runtime_dir(monkeypatch, tmp_path):
    cfg = _load(monkeypatch, tmp_path)
    rt = tmp_path / "rt"
    (rt / "prometheus").mkdir(parents=True)
    (rt / "prometheus" / "counter_123.db").write_bytes(b"x")
    (rt / "draining").write_text("1")
    cfg["on_starting"](None)
    assert (rt / "prometheus").is_dir() and (rt / "workers").is_dir()
    assert list((rt / "prometheus").iterdir()) == []
    assert not (rt / "draining").exists()


class _Log:
    def info(self, *a, **k):
        pass


class _Arbiter:
    def __init__(self):
        self.log = _Log()
        self.stock_called = False

    def handle_term(self):
        self.stock_called = True
        raise StopIteration


def test_sigterm_marks_the_container_draining_before_the_stock_handler(monkeypatch, tmp_path):
    cfg = _load(monkeypatch, tmp_path)
    arb = _Arbiter()
    cfg["when_ready"](arb)
    handler = getattr(arb, "handle_" + "term")     # how gunicorn dispatches SIGTERM
    with pytest.raises(StopIteration):
        handler()
    assert arb.stock_called
    assert (tmp_path / "rt" / "draining").exists()


def test_child_exit_forgets_the_workers_live_gauges_and_board_entry(monkeypatch, tmp_path):
    cfg = _load(monkeypatch, tmp_path)
    cfg["on_starting"](None)
    rt = tmp_path / "rt"
    live = rt / "prometheus" / "gauge_livesum_4321.db"
    live.write_bytes(b"")
    counter = rt / "prometheus" / "counter_4321.db"
    counter.write_bytes(b"")
    board = rt / "workers" / "4321.json"
    board.write_text("{}")

    class W:
        pid = 4321

    cfg["child_exit"](None, W())
    assert not live.exists() and not board.exists()
    assert counter.exists()          # a dead worker's counts stay in the totals


def test_healthcheck_reports_a_draining_container_healthy(monkeypatch, tmp_path):
    hc = runpy.run_path(str(BACKEND / "healthcheck.py"))
    conf = _load(monkeypatch, tmp_path)
    assert hc["RUNTIME_DIR_DEFAULT"] == conf["RUNTIME_DIR_DEFAULT"]
    rt = tmp_path / "rt"
    rt.mkdir(parents=True, exist_ok=True)
    assert hc["draining"](str(rt)) is False
    (rt / "draining").write_text("1")
    assert hc["draining"](str(rt)) is True
    monkeypatch.setenv("SRAN_RUNTIME_DIR", str(rt))
    assert hc["main"]() == 0                      # no HTTP call needed while draining
    (rt / "draining").unlink()
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: (_ for _ in ()).throw(OSError("refused")))
    assert hc["main"]() == 1


def test_the_image_execs_gunicorn_with_this_config():
    dockerfile = (BACKEND / "Dockerfile").read_text(encoding="utf-8")
    cmd = [l for l in dockerfile.splitlines() if l.startswith("CMD")]
    assert cmd == ['CMD ["sh", "/app/serve.sh"]']
    serve = (BACKEND / "serve.sh").read_text(encoding="utf-8")
    assert "python -m alembic upgrade head" in serve
    assert serve.rstrip().splitlines()[-1] == "exec gunicorn -c gunicorn.conf.py app.main:app"
    req = (BACKEND / "requirements.txt").read_text(encoding="utf-8")
    assert re.search(r"^gunicorn==\d", req, re.M), "gunicorn must be pinned"


@pytest.mark.skipif(COMPOSE is None, reason="environment: repo-root docker-compose.yml is not mounted")
def test_compose_waits_longer_than_the_drain_and_runs_the_image():
    import yaml  # noqa: PLC0415 — PyYAML ships with the test image

    doc = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    backend = doc["services"]["backend"]
    grace = backend["stop_grace_period"]
    assert grace.endswith("s")
    env = backend["environment"]
    drain = int(re.search(r":-(\d+)\}", env["GUNICORN_GRACEFUL_TIMEOUT"]).group(1))
    assert int(grace[:-1]) >= drain + 15, "Docker must not SIGKILL the drain"
    assert "command" not in backend                                   # the image's serve.sh
    assert "stop_signal" not in backend                               # SIGTERM: the drain path
    assert [v for v in backend.get("volumes", []) if str(v).startswith("./")] == []  # no source mount
    assert "/app/healthcheck.py" in " ".join(backend["healthcheck"]["test"])
    ops = doc["services"]["ops-watch"]
    assert [v for v in ops.get("volumes", []) if str(v).startswith("./backend")] == []
    assert ops.get("init") is True
