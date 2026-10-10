"""The domain glossary is `GLOSSARY.md`, and the old name is gone (Owner ruling, 2026-10-05).

The upstream skills renamed the glossary file, so this repo follows. A live file that still names the old file
sends a reader to a path that does not exist. ADRs are guides and were renamed with a plain
substitution; `scripts/archive/` holds recorded data and is left as it was.

Guard: GLOSSARY.md exists at the root, the old file does not, and no tracked file outside
`scripts/archive/` names the old file.
"""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OLD = "CONTEXT"  # joined below so this file never names the old file itself
PATTERN = re.compile(OLD + r"(-MAP|-FORMAT)?\.md")


def tracked():
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [p for p in out.split("\0") if p and (ROOT / p).is_file()]


def test_glossary_is_at_the_root_and_the_old_name_is_gone():
    assert (ROOT / "GLOSSARY.md").is_file()
    assert not (ROOT / (OLD + ".md")).exists()


def test_no_live_file_names_the_old_glossary():
    hits = []
    for p in tracked():
        if p.startswith("scripts/archive/"):
            continue
        try:
            text = (ROOT / p).read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if PATTERN.search(text):
            hits.append(p)
    assert not hits, f"files still name the old glossary file: {hits}"
