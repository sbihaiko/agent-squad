import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ADR_DIR = ROOT / "docs" / "adr"
INDEX = ADR_DIR / "README.md"


def test_every_adr_file_is_linked_from_the_index():
    linked = set(re.findall(r"\]\((\d{4}-[^)\s]+\.md)\)", INDEX.read_text()))
    files = {path.name for path in ADR_DIR.glob("[0-9][0-9][0-9][0-9]-*.md")}
    missing = sorted(files - linked)
    assert not missing, f"ADR files not linked from docs/adr/README.md: {missing}"


def test_two_adrs_never_share_a_number():
    """ADR-0164 was written twice on one day: one file about delivery, one about a dispatch's card write. The index linked
    both, so no reader could tell which the number meant, and a citation by number named two decisions. A number is one
    decision's address."""
    numbers = {}
    for path in sorted(ADR_DIR.glob("[0-9][0-9][0-9][0-9]-*.md")):
        numbers.setdefault(path.name[:4], []).append(path.name)
    shared = {number: names for number, names in numbers.items() if len(names) > 1}
    assert not shared, f"two ADRs share a number: {shared}"
