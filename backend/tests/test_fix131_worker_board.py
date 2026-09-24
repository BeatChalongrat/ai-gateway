"""FIX131 — /ready answers for the CONTAINER, whichever worker it lands on.

Under gunicorn the probe reaches one of N workers. Before FIX131, a request
stuck in worker B was invisible to a probe answered by worker A (the
healthcheck needs 3 failures in a row, so with 4 workers a stuck worker
almost never turned the container unhealthy), and ``checks.turns`` said
"never" on a worker that had not served a turn itself. Each worker now
publishes a small record on the worker board; /ready merges the others'.
"""

from __future__ import annotations

import asyncio
import json
import os
import time

import pytest
from fastapi.testclient import TestClient

from app.core import turn_pulse, worker_board
from app.main import app
from app.services import pg_activity
from app.services.pg_activity import ActivitySample


@pytest.fixture()
def board(tmp_path, monkeypatch):
    d = tmp_path / "workers"
    monkeypatch.setenv(worker_board.ENV, str(d))
    pg_activity._set_last(ActivitySample(at=time.time()))
    turn_pulse.reset()
    yield d
    pg_activity._last = None
    turn_pulse.reset()


def _peer(board_dir, pid: int, *, stuck: int = 0, count: int = 0, leader: bool = False,
          success_at: float | None = None, at: float | None = None) -> None:
    board_dir.mkdir(parents=True, exist_ok=True)
    rec = {"pid": pid, "at": time.time() if at is None else at, "leader": leader,
           "inflight": {"count": count, "oldest_age_s": 0.0, "stuck": stuck},
           "last_success_at": success_at, "last_attempt_at": success_at}
    (board_dir / f"{pid}.json").write_text(json.dumps(rec), encoding="utf-8")


def test_a_request_stuck_in_another_worker_makes_the_container_not_ready(board):
    _peer(board, 999_001, stuck=1, count=1)
    r = TestClient(app).get("/ready")
    body = r.json()
    assert r.status_code == 503
    assert "stuck_requests" in body["failures"]
    assert body["checks"]["inflight"]["stuck"] == 0            # this worker is fine
    assert body["checks"]["container"]["stuck"] == 1
    assert body["checks"]["container"]["workers"] == 2


def test_turns_are_the_containers_last_turn_not_this_workers(board):
    _peer(board, 999_002, success_at=time.time() - 3.0)
    body = TestClient(app).get("/ready").json()
    age = body["checks"]["turns"]["last_success_age_s"]
    assert age is not None and 2.0 <= age < 30.0


def test_the_leader_is_named_from_any_worker(board):
    _peer(board, 999_003, leader=True)
    _peer(board, 999_004)
    body = TestClient(app).get("/ready").json()
    assert body["checks"]["container"]["leader_pid"] == 999_003
    assert body["checks"]["container"]["workers"] == 3


def test_stale_or_broken_records_are_ignored(board):
    _peer(board, 999_005, stuck=3, at=time.time() - 3600)      # a worker killed long ago
    (board / "999006.json").write_text("{not json", encoding="utf-8")
    r = TestClient(app).get("/ready")
    assert "stuck_requests" not in r.json()["failures"]
    assert r.json()["checks"]["container"]["workers"] == 1


def test_without_a_board_ready_describes_this_process(monkeypatch):
    monkeypatch.delenv(worker_board.ENV, raising=False)
    pg_activity._set_last(ActivitySample(at=time.time()))
    try:
        body = TestClient(app).get("/ready").json()
        assert body["checks"]["container"]["workers"] == 1
        assert body["checks"]["container"]["leader_pid"] is None
    finally:
        pg_activity._last = None


def test_publish_and_peers_round_trip(board):
    assert worker_board.publish({"leader": True}, pid=4242) is True
    assert worker_board.publish({"leader": False})                 # this process
    peers = worker_board.peers()
    assert [p["pid"] for p in peers] == [4242]                     # self excluded
    worker_board.remove(4242)
    assert worker_board.peers() == []


@pytest.mark.asyncio
async def test_publisher_loop_publishes_and_cleans_up(board):
    task = asyncio.create_task(worker_board.publisher_loop(lambda: {"leader": False}, interval_s=0.01))
    for _ in range(100):
        await asyncio.sleep(0.01)
        if (board / f"{os.getpid()}.json").exists():
            break
    assert (board / f"{os.getpid()}.json").exists()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not (board / f"{os.getpid()}.json").exists()


def test_worker_record_carries_what_ready_merges():
    from app.api.routes.health import worker_record

    turn_pulse.reset()
    turn_pulse.mark("success")
    try:
        rec = worker_record()
        assert set(rec) >= {"inflight", "leader", "last_success_at", "last_attempt_at"}
        assert rec["inflight"]["stuck"] == 0 and rec["last_success_at"] is not None
    finally:
        turn_pulse.reset()


def test_turn_pulse_snapshot_merges_peers():
    turn_pulse.reset()
    now = time.time()
    snap = turn_pulse.snapshot([{"last_success_at": now - 10, "last_attempt_at": now - 1},
                                {"last_success_at": None, "last_attempt_at": "garbage"}])
    assert 9.0 <= snap["last_success_age_s"] <= 11.0
    assert snap["last_attempt_age_s"] <= 2.0
    assert turn_pulse.snapshot() == {"last_success_age_s": None, "last_attempt_age_s": None}
