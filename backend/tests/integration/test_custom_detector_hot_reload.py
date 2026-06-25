"""CustomDetectorLoader hot-reload + pub/sub tests (Sprint 6, D).

Uses a fake Redis/pubsub so no live broker is needed. The DB session factory
is the conftest-patched integration session (shared SQLite file), so rows
written via the service are visible to the loader's own session.
"""

import asyncio
import contextlib
import uuid

import pytest

from app.core.db import get_async_session_factory
from app.models.portal_user import PortalUserORM
from app.services.dlp.custom_detector_loader import (
    CUSTOM_DETECTOR_CHANNEL,
    CustomDetectorLoader,
    publish_reload,
)
from app.services.dlp.custom_detector_service import CustomDetectorService


class _FakePubSub:
    def __init__(self, messages):
        self._messages = list(messages)
        self.unsubscribed = False

    async def subscribe(self, channel):
        self._channel = channel

    async def unsubscribe(self, channel):
        self.unsubscribed = True

    async def get_message(self, ignore_subscribe_messages=False, timeout=None):
        # Deliver queued messages first, then behave like an idle channel.
        if self._messages:
            return self._messages.pop(0)
        await asyncio.sleep(0.01)
        return None


class _FakeRedis:
    def __init__(self, messages=None):
        self._messages = messages or []
        self.published = []

    def pubsub(self):
        return _FakePubSub(self._messages)

    async def publish(self, channel, message):
        self.published.append((channel, message))


async def _seed_active_detector(db) -> str:
    uid = f"u-{uuid.uuid4().hex[:8]}"
    db.add(PortalUserORM(id=uid, email=f"{uid}@e.com", password_hash="x"))
    await db.flush()
    svc = CustomDetectorService(db, redis_client=None)
    det = await svc.create(
        name=f"d-{uuid.uuid4().hex[:6]}", description=None, category="pii",
        detection_type="regex", pattern={"regex": r"\d{4}"}, severity="medium",
        default_action="mask",
        test_cases=[{"input": "1234", "expected_match": True}],
        created_by_user_id=uid,
    )
    await svc.activate(det.id, uid)
    return det.id


@pytest.mark.asyncio
async def test_cache_starts_empty():
    loader = CustomDetectorLoader(_FakeRedis(), get_async_session_factory())
    assert loader.get_cached_detectors() == []


@pytest.mark.asyncio
async def test_rebuild_loads_active_rows(async_db):
    det_id = await _seed_active_detector(async_db)
    loader = CustomDetectorLoader(_FakeRedis(), get_async_session_factory())
    await loader.rebuild()
    cached = loader.get_cached_detectors()
    # The shared integration DB is append-only across committed writes, so
    # assert membership rather than an exact count.
    assert det_id in {d._id for d in cached}


@pytest.mark.asyncio
async def test_rebuild_fail_safe_to_empty():
    class _BoomFactory:
        def __call__(self):
            raise RuntimeError("db down")

    loader = CustomDetectorLoader(_FakeRedis(), _BoomFactory())
    await loader.rebuild()  # must not raise
    assert loader.get_cached_detectors() == []


@pytest.mark.asyncio
async def test_pubsub_message_triggers_rebuild(async_db):
    det_id = await _seed_active_detector(async_db)
    # One reload message, then the listen generator ends and the loop returns.
    redis = _FakeRedis(messages=[{"type": "message", "data": "reload"}])
    loader = CustomDetectorLoader(redis, get_async_session_factory())
    assert loader.get_cached_detectors() == []  # not built yet

    task = asyncio.create_task(loader._subscribe_loop())
    try:
        for _ in range(100):  # up to ~1s for the message to drive a rebuild
            if loader.get_cached_detectors():
                break
            await asyncio.sleep(0.01)
    finally:
        loader._stopped = True
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert det_id in {d._id for d in loader.get_cached_detectors()}


@pytest.mark.asyncio
async def test_publish_reload_publishes_to_channel():
    redis = _FakeRedis()
    await publish_reload(redis)
    assert redis.published == [(CUSTOM_DETECTOR_CHANNEL, "reload")]
