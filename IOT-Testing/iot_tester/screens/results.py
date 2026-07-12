"""Results screen: view the session's accumulated PASS/FAIL results."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Static

from iot_tester.results_log import ResultRecorder


class ResultsScreen(Screen):
    BINDINGS = [("escape", "app.pop_screen", "Back to menu")]

    def __init__(self, recorder: ResultRecorder) -> None:
        super().__init__()
        self.recorder = recorder

    def compose(self) -> ComposeResult:
        yield Header()
        yield VerticalScroll(
            DataTable(id="results-table"),
            Static("", id="results-summary"),
        )
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#results-table", DataTable)
        table.add_columns("Component", "Case", "Result", "Note")
        for result in self.recorder.all_results():
            table.add_row(
                result.component, result.case, "PASS" if result.passed else "FAIL", result.note
            )
        passed, total = self.recorder.summary()
        summary_text = f"{passed}/{total} test cases passed"
        if self.recorder.all_results():
            log_path = self.recorder.flush()
            summary_text += f"\nLog: {log_path}"
        self.query_one("#results-summary", Static).update(summary_text)
