"""LogsScreen render/refresh behavior, driven headlessly via Textual's Pilot."""

from __future__ import annotations

import asyncio

from textual.app import App

from milo_brain.logbuf import RingBufferLogHandler
from milo_brain.tui.logs import LogsScreen


class _HostApp(App):
    def __init__(self, log_buffer):
        super().__init__()
        self.log_buffer = log_buffer

    async def on_mount(self) -> None:
        await self.push_screen(LogsScreen(self.log_buffer))


def _text(app):
    return str(app.screen.query_one("#log-text").content)


def test_shows_a_placeholder_when_the_buffer_is_empty():
    log_buffer = RingBufferLogHandler()

    async def scenario():
        app = _HostApp(log_buffer)
        async with app.run_test() as pilot:
            await pilot.pause()
            return _text(app)

    assert "no logs yet" in asyncio.run(scenario())


def test_shows_lines_already_in_the_buffer_on_mount():
    log_buffer = RingBufferLogHandler()
    log_buffer._buf.append("2026-01-01 INFO milo_brain.net.connector handshake failed: bad_pin")

    async def scenario():
        app = _HostApp(log_buffer)
        async with app.run_test() as pilot:
            await pilot.pause()
            return _text(app)

    assert "handshake failed: bad_pin" in asyncio.run(scenario())


def test_picks_up_new_lines_appended_after_mount():
    log_buffer = RingBufferLogHandler()

    async def scenario():
        app = _HostApp(log_buffer)
        async with app.run_test() as pilot:
            await pilot.pause()
            log_buffer._buf.append("a new line landed later")
            app.screen._refresh()  # same call the interval timer makes
            await pilot.pause()
            return _text(app)

    assert "a new line landed later" in asyncio.run(scenario())


def test_escape_returns_to_the_previous_screen():
    log_buffer = RingBufferLogHandler()

    async def scenario():
        app = _HostApp(log_buffer)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert isinstance(app.screen, LogsScreen)
            await pilot.press("escape")
            await pilot.pause()
            return isinstance(app.screen, LogsScreen)

    assert asyncio.run(scenario()) is False
