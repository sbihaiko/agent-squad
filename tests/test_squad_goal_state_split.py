"""One state directory per repository (contrib/squad-goal): every write a loop makes under the machine state directory
goes under that repository's own directory, or under the shared directory the seam names. Only the seam (address.sh)
spells the machine state directory; a shipped script or the skill that spells it builds a path the seam cannot split."""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1] / "contrib" / "squad-goal"
SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "contrib" / "squad-goal" / "scripts"
SEAM = ROOT / "scripts" / "address.sh"
SHARED_LITERAL = re.compile(r"\.cache/squad-goal|SG_STATE_BASE")


def shipped():
    yield ROOT / "SKILL.md"
    for f in sorted(SCRIPTS.iterdir()):
        if f.is_file() and f.suffix in {".sh", ".py"} and not f.name.startswith("test-") and f != SEAM:
            yield f


def test_only_the_seam_spells_the_machine_state_directory():
    hits = [f"{f.relative_to(ROOT)}:{i}: {line.strip()[:120]}"
            for f in shipped() for i, line in enumerate(f.read_text(errors="ignore").splitlines(), 1)
            if SHARED_LITERAL.search(line)]
    assert hits == [], "the machine state directory is spelled outside address.sh:\n" + "\n".join(hits)


def test_the_guard_finds_a_planted_literal(tmp_path):
    planted = tmp_path / "x.sh"
    planted.write_text('st=${SG_STATE:-$HOME/.cache/squad-goal}\n')
    assert SHARED_LITERAL.search(planted.read_text())


def test_the_seam_names_the_shared_directory_and_the_board_cache():
    text = SEAM.read_text()
    for fn in ("sg_state_base", "sg_state_dir", "sg_shared_dir", "sg_board_cache", "sg_health_label"):
        assert re.search(rf"^{fn}\(\)", text, re.M), f"address.sh defines no {fn}"
