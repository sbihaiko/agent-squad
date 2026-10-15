"""ADR-0145: the wave rules are stated, and each names its guard or says it is a draft."""
from pathlib import Path

ADR = next(Path(__file__).resolve().parent.parent.glob("docs/adr/0145-*.md"))


def test_adr_0145_states_each_wave_rule_and_the_draft_guard():
    text = ADR.read_text()
    for phrase in (
        "A wave has one branch",
        "rebase on the wave tip and `--ff-only`",
        "never enters the wave",
        "CI runs once per wave",
        "the last entrant first",
        "age cap",
        "Code Complete means the child is in the wave branch",
        "Done means the wave is in `dev`",
        "rebased on `dev` only before the final push",
        "8 to 11, 13 and 14 have no guard yet",   # rule 12 gained its guard (the board facts), the rest stay a draft
    ):
        assert phrase in text, phrase
