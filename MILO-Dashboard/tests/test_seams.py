"""Seam tests: run_cmd/read_file must never raise."""

from milo_dashboard.collectors import read_file, run_cmd


def test_run_cmd_missing_binary_returns_none():
    assert run_cmd(["definitely-not-a-real-binary-xyz"]) is None


def test_run_cmd_nonzero_exit_returns_none():
    import sys
    out = run_cmd([sys.executable, "-c", "import sys; sys.exit(3)"])
    assert out is None


def test_run_cmd_captures_stdout():
    import sys
    out = run_cmd([sys.executable, "-c", "print('hello')"])
    assert out is not None and out.strip() == "hello"


def test_read_file_missing_returns_none():
    assert read_file("/definitely/not/a/real/path/xyz") is None


def test_read_file_reads_text(tmp_path):
    p = tmp_path / "f.txt"
    p.write_text("data", encoding="utf-8")
    assert read_file(p) == "data"
