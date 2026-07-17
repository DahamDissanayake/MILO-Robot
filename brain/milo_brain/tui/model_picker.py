"""Modal model picker: lists installed Ollama models via GET /api/tags."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, ListItem, ListView, Static


class ModelPickerScreen(ModalScreen[str | None]):
    """Lists models installed in Ollama (GET {ollama_url}/api/tags -- not a
    subprocess call to the `ollama` CLI, consistent with how OllamaClient
    already talks to Ollama over HTTP, and it works whether or not `ollama`
    itself is on PATH). Picking one dismisses with its name; Escape
    dismisses with None (no change)."""

    DEFAULT_CSS = """
    ModelPickerScreen {
        align: center middle;
    }
    #model-box {
        width: 60;
        height: auto;
        max-height: 20;
        border: round $primary;
        padding: 1 2;
        background: $surface;
    }
    """

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, ollama_url: str, fetch_model_names=None):
        super().__init__()
        self.ollama_url = ollama_url.rstrip("/")
        self._fetch_model_names = fetch_model_names or _fetch_model_names_via_http
        self._names: list[str] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="model-box"):
            yield Static("Select a model (installed in Ollama):")
            yield ListView(id="model-list")

    async def on_mount(self) -> None:
        list_view = self.query_one("#model-list", ListView)
        self._names = await self._fetch_model_names(self.ollama_url)
        if not self._names:
            await list_view.append(ListItem(Label("No models found -- is Ollama running?")))
            return
        for name in self._names:
            await list_view.append(ListItem(Label(name)))

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if not self._names:
            return
        self.dismiss(self._names[event.list_view.index])

    def action_cancel(self) -> None:
        self.dismiss(None)


async def _fetch_model_names_via_http(ollama_url: str) -> list[str]:
    import httpx

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(f"{ollama_url}/api/tags")
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPError:
        return []
    return [m["name"] for m in data.get("models", [])]
