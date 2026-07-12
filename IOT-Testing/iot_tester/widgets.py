"""Shared PASS/FAIL capture widget used by every interactive test screen.

A screen's test coroutine calls ``passed, note = await ask_pass_fail(container,
question)``: it mounts a PassFailPrompt, waits for the tester to click PASS or
type a note and submit after FAIL, then removes the prompt and returns.
"""

from __future__ import annotations

import asyncio

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widget import Widget
from textual.widgets import Button, Input, Static


class PassFailPrompt(Widget):
    """A question with PASS/FAIL buttons; FAIL reveals a note Input before resolving."""

    DEFAULT_CSS = """
    PassFailPrompt Input.hidden {
        display: none;
    }
    """

    def __init__(self, question: str) -> None:
        super().__init__()
        self._question = question
        self._answer: asyncio.Future[tuple[bool, str]] = asyncio.get_running_loop().create_future()

    def compose(self) -> ComposeResult:
        yield Static(self._question, classes="prompt-question")
        yield Horizontal(
            Button("PASS", id="pass-btn", variant="success"),
            Button("FAIL", id="fail-btn", variant="error"),
            classes="prompt-buttons"
        )
        yield Input(
            placeholder="What went wrong? (Enter to submit)", id="note-input", classes="hidden"
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "pass-btn":
            if not self._answer.done():
                self._answer.set_result((True, ""))
        elif event.button.id == "fail-btn":
            note_input = self.query_one("#note-input", Input)
            note_input.remove_class("hidden")
            note_input.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        if not self._answer.done():
            self._answer.set_result((False, event.value))

    async def wait_for_answer(self) -> tuple[bool, str]:
        return await self._answer


async def ask_pass_fail(container: Widget, question: str) -> tuple[bool, str]:
    prompt = PassFailPrompt(question)
    await container.mount(prompt)
    result = await prompt.wait_for_answer()
    await prompt.remove()
    return result
