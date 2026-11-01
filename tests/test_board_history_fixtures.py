import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "contrib" / "squad-goal" / "scripts" / "fixtures" / "board-history"
NOTE = ROOT / "docs" / "board-history-coverage.md"
CASES = ["created-in-todo", "added-no-status", "removed-readded", "reopened", "reparented", "label-added", "label-removed"]
NOT_ON_BOARD = ["added-no-status", "reparented"]
SECRET = re.compile(r"ghp_|github_pat_|Authorization|Bearer ")


def test_every_case_has_a_fixture_or_is_named_absent():
    for case in CASES:
        path = FIXTURES / f"{case}.json"
        if case in NOT_ON_BOARD:
            assert not path.exists(), f"{case} is absent on the board, so no fixture may stand for it"
        else:
            assert path.is_file(), f"missing fixture {path.name}"


def test_fixtures_parse_as_json():
    for case in CASES:
        if case in NOT_ON_BOARD:
            continue
        json.loads((FIXTURES / f"{case}.json").read_text(encoding="utf-8"))


def test_fixtures_carry_no_token_or_authorization_value():
    for path in sorted(FIXTURES.glob("*.json")):
        assert not SECRET.search(path.read_text(encoding="utf-8")), f"{path.name} carries a token or authorization value"


def test_note_names_the_absent_case_and_the_measurements():
    text = NOTE.read_text(encoding="utf-8")
    assert "re-parent" in text.lower() and "not on the board" in text.lower()
    for word in ("Status event", "updatedAt", "node limit", "points"):
        assert word in text, f"note lacks {word!r}"
    assert not re.search(r"ADR-?\d|\bADR\b", text), "the note is not an ADR and carries no ADR number"


def test_note_is_not_in_the_adr_index():
    assert "board-history-coverage" not in (ROOT / "docs" / "adr" / "README.md").read_text(encoding="utf-8")
