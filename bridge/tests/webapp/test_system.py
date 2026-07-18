import asyncio

import milo_bridge.webapp.api.system as system_mod

from .client_helpers import authed_client
from .fakes import make_deps


async def test_get_crashes_returns_count_and_entries():
    deps = make_deps()
    try:
        raise ValueError("boom")
    except ValueError as exc:
        deps.crash_log.record("process", exc)
    client = await authed_client(deps)
    try:
        data = await (await client.get("/api/crashes")).json()
        assert data["count"] == 1
        assert data["entries"][0]["error"] == "ValueError: boom"
    finally:
        await client.close()


async def test_post_restart_clears_crash_log_and_schedules_reboot(monkeypatch):
    calls = []

    async def fake_run(*args):
        calls.append(args)

    monkeypatch.setattr(system_mod, "_run", fake_run)
    monkeypatch.setattr(system_mod, "REBOOT_DELAY_S", 0)

    deps = make_deps()
    try:
        raise ValueError("boom")
    except ValueError as exc:
        deps.crash_log.record("process", exc)
    assert deps.crash_log.count() == 1

    client = await authed_client(deps)
    try:
        resp = await client.post("/api/system/restart")
        assert await resp.json() == {"ok": True}
        assert deps.crash_log.count() == 0  # cleared immediately, not deferred
        await asyncio.sleep(0.05)  # let the deferred task run (delay set to 0)
        assert calls == [("sudo", "/usr/bin/systemctl", "reboot")]
    finally:
        await client.close()


async def test_post_shutdown_does_not_clear_crash_log(monkeypatch):
    calls = []

    async def fake_run(*args):
        calls.append(args)

    monkeypatch.setattr(system_mod, "_run", fake_run)
    monkeypatch.setattr(system_mod, "REBOOT_DELAY_S", 0)

    deps = make_deps()
    try:
        raise ValueError("boom")
    except ValueError as exc:
        deps.crash_log.record("process", exc)
    assert deps.crash_log.count() == 1

    client = await authed_client(deps)
    try:
        resp = await client.post("/api/system/shutdown")
        assert await resp.json() == {"ok": True}
        assert deps.crash_log.count() == 1  # NOT cleared
        await asyncio.sleep(0.05)
        assert calls == [("sudo", "/usr/bin/systemctl", "poweroff")]
    finally:
        await client.close()


async def test_deferred_system_command_logs_and_swallows_a_failing_command(monkeypatch, caplog):
    async def fake_run(*args):
        raise OSError("command not found")

    monkeypatch.setattr(system_mod, "_run", fake_run)
    monkeypatch.setattr(system_mod, "REBOOT_DELAY_S", 0)

    await system_mod._deferred_system_command("sudo", "/usr/bin/systemctl", "reboot")
    # must not raise -- a failed system command shouldn't crash the caller
