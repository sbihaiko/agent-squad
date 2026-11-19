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
        "lands empty and runs no check",   # rule 11: an empty wave carries no child commit a red check could blame (#449)
        "age cap",
        "Code Complete means the child is in the wave branch",
        "Done means the wave is in `dev`",
        "rebased on `dev` only before the final push",
        "the review of a run child's own diff, read in its own worktree",   # rule 9's review, added with #448
        "A child of the loop is reviewed by its own run",   # rule 9, rewritten with #601: the run's review gate
        "(cases L8 to L11)",   # rule 9's guard, re-anchored with #601
        "guard rules 11 and 13",   # rules 11 (a red wave check names its child) and 13 (a wave ages out) gained their guard
        "1, 4, 5, 6, 8 and 14 have no guard yet",   # rules 9, 10, 11, 12 and 13 gained their guards; rule 2 gained its guard too; the rest stay a draft
    ):
        assert phrase in text, phrase
