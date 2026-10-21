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
