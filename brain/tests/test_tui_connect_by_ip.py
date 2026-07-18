"""ConnectByIpScreen submit/cancel behavior, driven headlessly via Textual's Pilot."""

from __future__ import annotations

import asyncio

from textual.app import App

from milo_brain.tui.connect_by_ip import ConnectByIpScreen


class _HostApp(App):
    def __init__(self):
        super().__init__()
        self.result: str | None = "not-set"

    async def run_prompt(self) -> None:
        self.result = await self.push_screen_wait(ConnectByIpScreen())


def test_submitting_an_ip_dismisses_with_its_value():
    async def scenario():
        app = _HostApp()
        async with app.run_test() as pilot:
            app.run_worker(app.run_prompt())
            await pilot.pause()
            await pilot.click("#ip-input")
            await pilot.press(*"192.168.1.15")
            await pilot.press("enter")
            await pilot.pause()
        return app.result

    assert asyncio.run(scenario()) == "192.168.1.15"


def test_submitting_an_ip_with_port_dismisses_with_its_value():
    async def scenario():
        app = _HostApp()
        async with app.run_test() as pilot:
            app.run_worker(app.run_prompt())
            await pilot.pause()
            await pilot.click("#ip-input")
            await pilot.press(*"192.168.1.15:8765")
            await pilot.press("enter")
            await pilot.pause()
        return app.result

    assert asyncio.run(scenario()) == "192.168.1.15:8765"


def test_escape_dismisses_with_none():
    async def scenario():
        app = _HostApp()
        async with app.run_test() as pilot:
            app.run_worker(app.run_prompt())
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
        return app.result

    assert asyncio.run(scenario()) is None


def test_submitting_blank_input_dismisses_with_none():
    async def scenario():
        app = _HostApp()
        async with app.run_test() as pilot:
            app.run_worker(app.run_prompt())
            await pilot.pause()
            await pilot.click("#ip-input")
            await pilot.press("enter")
            await pilot.pause()
        return app.result

    assert asyncio.run(scenario()) is None
