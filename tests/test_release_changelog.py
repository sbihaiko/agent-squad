"""The version the plugin reports and the CHANGELOG must agree, and the ADR-0273 series must be in it (Refs #1396)."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _version() -> str:
    return json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())["version"]


def _section(version: str) -> str:
    text = (ROOT / "CHANGELOG.md").read_text()
    m = re.search(rf"^## {re.escape(version)} .*?(?=^## |\Z)", text, re.S | re.M)
    assert m, f"CHANGELOG has no section for plugin version {version}"
    return m.group(0)


def test_changelog_leads_with_the_plugin_version():
    first = re.search(r"^## (\d+\.\d+\.\d+)", (ROOT / "CHANGELOG.md").read_text(), re.M)
    assert first and first.group(1) == _version()


def test_version_was_bumped_past_the_pre_series_release():
    assert tuple(map(int, _version().split("."))) > (0, 2, 25)


def test_changelog_names_every_slice_of_the_adr_0273_series():
    sec = _section(_version())
    assert "ADR-0273" in sec
    for issue in ("#1393", "#1394", "#1395", "#1396"):
        assert issue in sec, f"{issue} missing from the {_version()} section"
