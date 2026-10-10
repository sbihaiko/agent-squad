"""The subject's answer key asserts what its issue text states.

The 24 held-out tests in `bench/subject/acceptance/` were authored one issue at a time against the base tree.
Ten assertions demanded more than their issue text states, in five groups. The answer key re-anchors each to the
property its text states, so the 24 files can pass together on one tree. The pins below make a further edit a
deliberate act: a squad run that "fixes" a held-out test to pass cannot move one unnoticed.

Guard for the answer-key rule.
"""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KEY = ROOT / "bench" / "subject" / "acceptance"
INDEX = ROOT / "bench" / "subject" / "issues.json"

# sha256[:16] of each held-out test, as re-cut on 2026-10-02.
PINS = {
    "test_issue_01.py": "5ae690d0be971987",
    "test_issue_02.py": "5b5a577b1385c37e",
    "test_issue_03.py": "be88ce8b2ad95dcc",
    "test_issue_04.py": "083f59c584288e42",
    "test_issue_05.py": "6b2722a9e3bd714f",
    "test_issue_06.py": "398eee2bc56e828f",
    "test_issue_07.py": "6a9c7599c7c2cd56",
    "test_issue_08.py": "aa9655cb831afa12",
    "test_issue_09.py": "ad4d7e2798dbc16d",
    "test_issue_10.py": "eba85e8454ffe156",
    "test_issue_11.py": "a230f0a68f129e8f",
    "test_issue_12.py": "badacec91730d3fd",
    "test_issue_13.py": "a5c4821051107186",
    "test_issue_14.py": "0a32af7be3c70e17",
    "test_issue_15.py": "0f3e7af38a094844",
    "test_issue_16.py": "7c7f895868ad1802",
    "test_issue_17.py": "63913348a61c685c",
    "test_issue_18.py": "7e6a206233fd397f",
    "test_issue_19.py": "7847f828dd746e15",
    "test_issue_20.py": "3322efabae1ad9fc",
    "test_issue_21.py": "920b887813bb504d",
    "test_issue_22.py": "b1e375616da21a51",
    "test_issue_23.py": "40c54bfc392fc635",
    "test_issue_24.py": "24957b074ff37365",
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def test_every_held_out_test_still_hashes_to_its_pin():
    """An edit to a held-out test moves its hash. Update the pin deliberately, in the same commit as the re-cut."""
    got = {p.name: digest(p) for p in sorted(KEY.glob("test_issue_*.py"))}
    assert sorted(got) == sorted(PINS), f"the key gained or lost a file: {sorted(set(got) ^ set(PINS))}"
    moved = {n: (PINS[n], got[n]) for n in PINS if got[n] != PINS[n]}
    assert moved == {}, moved


def test_the_index_lists_the_same_24_issues_and_the_file_the_pins_name():
    rows = json.loads(INDEX.read_text())
    assert [r["issue"] for r in rows] == list(range(1, 25))
    assert sorted(Path(r["acceptance_test"]).name for r in rows) == sorted(PINS)
