"""Shared PASS/FAIL result capture for every IOT-Testing screen.

Every screen records through one ResultRecorder instance, constructed once
in app.py and passed down. flush() is called after every record() so the
session log on disk is always current, even if the app exits abnormally.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class TestResult:
    component: str
    case: str
    passed: bool
    note: str = ""


class ResultRecorder:
    def __init__(self, results_dir: Path, run_started: datetime) -> None:
        self.results_dir = Path(results_dir)
        self.run_started = run_started
        self._results: list[TestResult] = []

    def record(self, component: str, case: str, passed: bool, note: str = "") -> None:
        self._results.append(TestResult(component, case, passed, note))

    def all_results(self) -> list[TestResult]:
        return list(self._results)

    def summary(self) -> tuple[int, int]:
        total = len(self._results)
        passed = sum(1 for r in self._results if r.passed)
        return passed, total

    def flush(self) -> Path:
        self.results_dir.mkdir(parents=True, exist_ok=True)
        timestamp = self.run_started.strftime("%Y%m%dT%H%M%SZ")
        log_path = self.results_dir / f"session-{timestamp}.log"
        passed, total = self.summary()
        lines = [
            "MILO IOT-Testing -- Session Log",
            f"Run: {self.run_started.isoformat()}",
            "",
        ]
        current_component = None
        for r in self._results:
            if r.component != current_component:
                lines.append(r.component)
                current_component = r.component
            status = "PASS" if r.passed else "FAIL"
            note = f"   note: {r.note}" if r.note else ""
            lines.append(f"  {r.case:<28} {status}{note}")
        lines.append("")
        lines.append(f"Summary: {passed}/{total} test cases passed")
        failures = [r for r in self._results if not r.passed]
        if failures:
            lines.append(
                "Failed: " + ", ".join(f"{r.component} {r.case.split()[0]}" for r in failures)
            )
        log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return log_path
