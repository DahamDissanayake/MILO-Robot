"""ModelPickerScreen selection/cancel behavior, and the /api/tags fetch helper."""

from __future__ import annotations

import asyncio

import httpx
from textual.app import App

from milo_brain.tui.model_picker import ModelPickerScreen, _fetch_model_names_via_http


class _HostApp(App):
    def __init__(self, fetch_model_names):
        super().__init__()
        self.fetch_model_names = fetch_model_names
        self.result: str | None = "not-set"

    async def run_picker(self) -> None:
        self.result = await self.push_screen_wait(
            ModelPickerScreen("http://127.0.0.1:11434", fetch_model_names=self.fetch_model_names)
        )


def test_selecting_the_first_item_dismisses_with_its_name():
    async def fake_fetch(url):
        return ["llama3.2:3b", "llama3.1:8b"]

    async def scenario():
        app = _HostApp(fake_fetch)
        async with app.run_test() as pilot:
            app.run_worker(app.run_picker())
            await pilot.pause()
            # ListView starts with nothing highlighted; one "down" highlights
            # the first item (verified empirically -- it does NOT skip to
            # the second item).
            await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause()
        return app.result

    assert asyncio.run(scenario()) == "llama3.2:3b"


def test_selecting_the_second_item_dismisses_with_its_name():
    async def fake_fetch(url):
        return ["llama3.2:3b", "llama3.1:8b"]

    async def scenario():
        app = _HostApp(fake_fetch)
        async with app.run_test() as pilot:
            app.run_worker(app.run_picker())
            await pilot.pause()
            await pilot.press("down")
            await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause()
        return app.result

    assert asyncio.run(scenario()) == "llama3.1:8b"


def test_escape_dismisses_with_none():
    async def fake_fetch(url):
        return ["llama3.2:3b"]

    async def scenario():
        app = _HostApp(fake_fetch)
        async with app.run_test() as pilot:
            app.run_worker(app.run_picker())
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
        return app.result

    assert asyncio.run(scenario()) is None


def test_empty_model_list_shows_a_message_instead_of_crashing():
    async def fake_fetch(url):
        return []

    async def scenario():
        app = _HostApp(fake_fetch)
        async with app.run_test() as pilot:
            app.run_worker(app.run_picker())
            await pilot.pause()
            label = app.screen.query_one("Label")
            return str(label.content)

    assert "No models found" in asyncio.run(scenario())


def test_fetch_model_names_via_http_parses_the_tags_response(monkeypatch):
    class _FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"models": [{"name": "llama3.2:3b"}, {"name": "llama3.1:8b"}]}

    async def fake_get(self, url):
        return _FakeResponse()

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    names = asyncio.run(_fetch_model_names_via_http("http://127.0.0.1:11434"))
    assert names == ["llama3.2:3b", "llama3.1:8b"]


def test_fetch_model_names_via_http_returns_empty_list_on_error(monkeypatch):
    async def fake_get(self, url):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    names = asyncio.run(_fetch_model_names_via_http("http://127.0.0.1:11434"))
    assert names == []
