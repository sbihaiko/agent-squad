import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HEADING = "## Tailnet access"
STEP_MARKERS = (
    ("Install Tailscale", "sign in"),
    ("MagicDNS", "HTTPS"),
    ("ACL",),
    ("squad tailnet serve-command",),
    ("tailnet.host", "tailnet.allow", "squad config set"),
    ("Funnel", "squad tailnet check"),
)


def _section():
    text = (ROOT / "docs" / "quickstart.md").read_text()
    match = re.search(rf"^{re.escape(HEADING)}\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    assert match, f"docs/quickstart.md has no '{HEADING}' section"
    return match.group(1)


def test_the_six_owner_steps_appear_in_order():
    items = re.findall(r"^(\d+)\. (.+)$", _section(), re.M)
    assert [number for number, _ in items] == ["1", "2", "3", "4", "5", "6"]
    for (_, body), markers in zip(items, STEP_MARKERS):
        for marker in markers:
            assert marker in body, f"step missing '{marker}': {body}"


def test_the_section_names_the_commands_and_settings():
    section = _section()
    for name in ("squad tailnet serve-command", "squad tailnet check", "tailnet.host", "tailnet.allow"):
        assert name in section, f"section does not name '{name}'"


def test_the_section_holds_no_tailscale_funnel_command():
    assert not re.search(r"tailscale\s+funnel", _section(), re.I)


def test_the_section_keeps_the_loopback_bind_and_the_local_editor():
    section = _section()
    assert "The servers keep their loopback bind." in section
    assert "The editor stays a local `file://` page." in section
