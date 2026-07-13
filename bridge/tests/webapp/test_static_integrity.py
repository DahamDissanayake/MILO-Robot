"""Every file referenced by index.html and registry.js must exist —
guards the 'adding a card = one file + one line' workflow."""
import re
from pathlib import Path

STATIC = Path("bridge/milo_bridge/webapp/static")


def test_index_references_exist():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    for ref in re.findall(r'(?:href|src)="/static/([^"]+)"', html):
        assert (STATIC / ref).exists(), f"index.html references missing {ref}"


def test_registry_imports_exist():
    js = (STATIC / "js" / "registry.js").read_text(encoding="utf-8")
    for ref in re.findall(r"from\s+['\"]\./(.+?)['\"]", js):
        assert (STATIC / "js" / ref).exists(), f"registry.js imports missing {ref}"


def test_login_page_references_exist():
    html = (STATIC / "login.html").read_text(encoding="utf-8")
    for ref in re.findall(r'(?:href|src)="/static/([^"]+)"', html):
        assert (STATIC / ref).exists(), f"login.html references missing {ref}"


def test_shell_files_exist():
    for f in ["index.html", "css/theme.css", "css/grid.css", "js/main.js",
              "js/registry.js", "js/bus.js", "js/grid.js",
              "js/cards/status.js", "js/cards/log.js"]:
        assert (STATIC / f).exists(), f"missing {f}"
