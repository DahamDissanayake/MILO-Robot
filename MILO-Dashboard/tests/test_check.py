from milo_dashboard.check import render_report


def test_render_report_contains_all_sections():
    report = render_report()
    for heading in ("SYSTEM", "NETWORK", "STORAGE", "SERVICES"):
        assert heading in report
    assert "CPU" in report
