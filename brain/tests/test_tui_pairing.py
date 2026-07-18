"""PairingPinScreen submit/cancel behavior, driven headlessly via Textual's Pilot."""

from __future__ import annotations

import asyncio

from textual.app import App

from milo_brain.tui.pairing import PairingPinScreen


class _HostApp(App):
    def __init__(self):
        super().__init__()
        self.result: str | None = "not-set"

    async def run_pairing(self) -> None:
        self.result = await self.push_screen_wait(PairingPinScreen("milo-1"))


def test_submitting_the_pin_dismisses_with_its_value():
    async def scenario():
        app = _HostApp()
        async with app.run_test() as pilot:
            app.run_worker(app.run_pairing())
            await pilot.pause()
            await pilot.click("#pin-input")
            await pilot.press(*"1234")
            await pilot.press("enter")
            await pilot.pause()
        return app.result

    assert asyncio.run(scenario()) == "1234"


def test_escape_dismisses_with_none():
    async def scenario():
        app = _HostApp()
        async with app.run_test() as pilot:
            app.run_worker(app.run_pairing())
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
        return app.result

    assert asyncio.run(scenario()) is None


def test_submitting_blank_input_dismisses_with_none():
    async def scenario():
        app = _HostApp()
        async with app.run_test() as pilot:
            app.run_worker(app.run_pairing())
            await pilot.pause()
            await pilot.click("#pin-input")
            await pilot.press("enter")
            await pilot.pause()
        return app.result

    assert asyncio.run(scenario()) is None
