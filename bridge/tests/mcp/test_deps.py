import asyncio

from milo_bridge.mcp.deps import MovementGuard


def test_guard_is_free_until_a_coroutine_is_running():
    guard = MovementGuard()
    assert guard.busy() is False


def test_guard_reports_busy_while_the_task_runs_and_frees_after():
    async def main():
        guard = MovementGuard()
        started = asyncio.Event()
        finish = asyncio.Event()

        async def slow():
            started.set()
            await finish.wait()

        guard.start(slow())
        await started.wait()
        assert guard.busy() is True

        finish.set()
        await asyncio.sleep(0)  # let the task actually finish
        await asyncio.sleep(0)
        assert guard.busy() is False

    asyncio.run(main())
