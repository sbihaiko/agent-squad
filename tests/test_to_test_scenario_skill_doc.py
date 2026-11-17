"""#1478 (ADR-0275 slice G): the to-test-scenario skill describes the layout, and every command it shows exists."""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL = (ROOT / "vendor" / "skills" / "skills" / "to-test-scenario" / "SKILL.md").read_text()


def _shown_commands():
    """Each `python3 -m squad.to_test_scenario <command> ...` in a fenced block, as (command, [--flags])."""
    blocks = re.findall(r"```bash\n(.*?)```", SKILL, re.S)
    out = []
    for block in blocks:
        for call in re.findall(r"python3 -m squad\.to_test_scenario\s+([^\n]*)", re.sub(r"\\\n", " ", block)):
            out.append((call.split()[0], re.findall(r"--[a-z][a-z-]*", call)))
    return out


def _help(*args):
    run = subprocess.run([sys.executable, "-m", "squad.to_test_scenario", *args, "--help"],
                         cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stderr
    return run.stdout


def test_every_command_and_flag_the_skill_shows_is_in_the_cli_help():
    shown = _shown_commands()
    assert {c for c, _ in shown} == {"publish", "start-run", "write-back"}
    for command, flags in shown:
        text = _help(command)
        for flag in flags:
            assert flag in text, f"`{command} {flag}` is shown in SKILL.md but missing from --help"


def test_the_skill_shows_the_dry_run_flag_of_publish():
    assert any("--dry-run" in flags for command, flags in _shown_commands() if command == "publish")


def test_the_skill_describes_the_areas_layout_and_the_area_rule():
    for phrase in ("Area issue", "📋 Cases", "📅 Runs", "`Test-Area`", "`Test-Run`", "`area`", "`objective`",
                   "batch id up to its first `-`", "(no objective stated)"):
        assert phrase in SKILL, phrase


def test_the_skill_lists_the_verdict_emoji_and_their_order():
    for emoji in ("✅", "❌", "🟡", "👤", "⚪"):
        assert emoji in SKILL
    assert "❌ > 🟡 > 👤 > ⚪ > ✅" in SKILL


def test_the_skill_says_the_write_back_files_bugs_and_the_map_is_committed():
    assert "never files a bug itself" not in SKILL
    assert "one bug per failing case" in SKILL.lower()
    assert "commit" in SKILL.lower() and "after a write-back" in SKILL
    assert "Only the parent" in SKILL


def test_the_skill_describes_the_migration_of_a_flat_scenario():
    assert "flat scenario" in SKILL and "re-parent" in SKILL


def test_the_skill_writes_no_wave_label():
    assert "This repository writes no wave label" in SKILL, (
        "the repo patch states that the skill writes no wave label (#2132)")


def test_the_skill_keeps_the_blocked_by_edge_of_a_setup_case():
    assert "order of a case comes from its" in SKILL, "the setup case orders a case by its `## Blocked by` edge (#2132)"
    assert "`## Blocked by` edge" in SKILL and "native" in SKILL.lower()
