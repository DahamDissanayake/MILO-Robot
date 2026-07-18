"""main.py's two crash-capture points: the asyncio exception handler
(background-task failures) and run()'s top-level crash handling (full
process crashes)."""

from __future__ import annotations

import asyncio

import pytest

from milo_bridge.crashlog import CrashLog
from milo_bridge.main import _make_crash_exception_handler


def test_crash_exception_handler_records_task_failures_and_calls_default(tmp_path):
    crash_log = CrashLog(tmp_path / "crashes.log")
    default_calls = []

    async def scenario():
        loop = asyncio.get_running_loop()
        loop.default_exception_handler = default_calls.append
        loop.set_exception_handler(_make_crash_exception_handler(crash_log))
        loop.call_exception_handler({
            "message": "Task exception was never retrieved",
            "exception": RuntimeError("background task failure"),
        })

    asyncio.run(scenario())
    assert crash_log.count() == 1
    entry = crash_log.entries()[0]
    assert entry["kind"] == "task"
    assert entry["context"] == "Task exception was never retrieved"
    assert entry["error"] == "RuntimeError: background task failure"
    assert len(default_calls) == 1
    assert default_calls[0]["message"] == "Task exception was never retrieved"


def test_crash_exception_handler_ignores_context_without_an_exception(tmp_path):
    """The context dict doesn't always carry an 'exception' key (e.g. a
    plain warning-level context) -- must not crash trying to record None,
    and must still forward to the default handler."""
    crash_log = CrashLog(tmp_path / "crashes.log")
    default_calls = []

    async def scenario():
        loop = asyncio.get_running_loop()
        loop.default_exception_handler = default_calls.append
        loop.set_exception_handler(_make_crash_exception_handler(crash_log))
        loop.call_exception_handler({"message": "some non-exception warning"})

    asyncio.run(scenario())
    assert crash_log.count() == 0
    assert len(default_calls) == 1


def test_run_records_a_full_process_crash_before_reraising(monkeypatch, tmp_path):
    from milo_bridge import main as main_mod
    from milo_bridge.config import BridgeConfig

    cfg = BridgeConfig(robot_id="r", robot_name="milo", data_dir=str(tmp_path))
    monkeypatch.setattr(main_mod.BridgeConfig, "load", staticmethod(lambda: cfg))

    async def failing_main():
        raise RuntimeError("boot exploded")

    monkeypatch.setattr(main_mod, "main", failing_main)

    with pytest.raises(RuntimeError, match="boot exploded"):
        main_mod.run()

    crash_log = main_mod.CrashLog(tmp_path / "crashes.log")
    entries = crash_log.entries()
    assert len(entries) == 1
    assert entries[0]["kind"] == "process"
    assert entries[0]["error"] == "RuntimeError: boot exploded"
