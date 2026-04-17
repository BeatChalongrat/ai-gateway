"""P10-03: Background task runner tests."""

import pytest

from app.services.background_tasks import BackgroundTaskRunner


class TestBackgroundTaskRunner:
    @pytest.mark.asyncio
    async def test_enqueue_and_process(self):
        runner = BackgroundTaskRunner()
        results = []

        async def task(val):
            results.append(val)

        runner.enqueue(task, 1)
        runner.enqueue(task, 2)
        count = await runner.process_all()
        assert count == 2
        assert results == [1, 2]

    @pytest.mark.asyncio
    async def test_error_isolation(self):
        runner = BackgroundTaskRunner()
        results = []

        async def good(val):
            results.append(val)

        async def bad():
            raise ValueError("boom")

        runner.enqueue(good, "a")
        runner.enqueue(bad)
        runner.enqueue(good, "b")
        await runner.process_all()
        assert results == ["a", "b"]
        assert runner.stats["failed"] == 1

    @pytest.mark.asyncio
    async def test_queue_bounded(self):
        runner = BackgroundTaskRunner(max_queue=2)

        async def noop():
            pass

        assert runner.enqueue(noop) is True
        assert runner.enqueue(noop) is True
        assert runner.enqueue(noop) is False  # queue full

    @pytest.mark.asyncio
    async def test_stats(self):
        runner = BackgroundTaskRunner()

        async def noop():
            pass

        runner.enqueue(noop)
        runner.enqueue(noop)
        assert runner.stats["pending"] == 2
        await runner.process_all()
        assert runner.stats["pending"] == 0
        assert runner.stats["processed"] == 2

    @pytest.mark.asyncio
    async def test_empty_process(self):
        runner = BackgroundTaskRunner()
        count = await runner.process_all()
        assert count == 0
