"""The delta carry's map is a guard itself (ADR-0156): a line that names a path no file matches, or a command no file
holds, would refuse or silently skip a covered change. Every pattern resolves, and every command is `SELF` or a path the
repository holds."""
import glob
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAP = ROOT / "contrib/squad-goal/scripts/delta-guards.txt"


def rows():
    out = []
    for line in MAP.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        pattern, _, command = line.partition("\t")
        out.append((pattern.strip(), command.strip()))
    return out


def test_every_map_line_has_a_pattern_and_a_command():
    assert rows() and all(p and c for p, c in rows()), "a line without a TAB or without a command"


def test_every_pattern_names_at_least_one_repository_path():
    missing = [p for p, _ in rows() if not glob.glob(str(ROOT / p), recursive=True)]
    assert missing == [], f"a pattern covers nothing: {missing}"


def test_every_command_is_self_or_a_file_the_repository_holds():
    missing = [c for _, c in rows() if c != "SELF" and not (ROOT / c).exists()]
    assert missing == [], f"a command names no file: {missing}"
