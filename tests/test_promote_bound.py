"""A proof is bounded at fifteen minutes, and the delta answers past it (ADR-0163).

The bound is a rule about the script that runs the proof, not about this test: the default bound and the branch that runs
when it is reached are read back from the script itself.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
PASS = ROOT / "contrib/squad-goal/scripts/promote-pass.sh"
BOUND = 15 * 60


def test_the_default_bound_is_fifteen_minutes():
    text = PASS.read_text()
    m = re.search(r"bound=\$\{SG_PROMOTE_BOUND_SECONDS:-(\d+)\}", text)
    assert m, "the script names no bound for the attempt"
    assert int(m.group(1)) == BOUND, f"the proof's default bound is {m.group(1)}s, not {BOUND}s"


def test_the_suite_is_bounded_by_what_the_attempt_has_left():
    """A bound on the suite alone lets an attempt run the suite to its bound and only then start the fallback."""
    text = PASS.read_text()
    m = re.search(r"^  rbound=\$reserve_s; dline=.*$", text, re.M)
    assert m, "the suite's deadline is not read back"
    assert "- keep -" in m.group(0) and "t0" in m.group(0), m.group(0)


def test_past_the_bound_the_delta_answers_for_the_tip():
    text = PASS.read_text()
    i = text.index('if [ "$rc" = 124 ]; then\n    kill "$mon"')
    branch = text[i:text.index('if [ "$rc" != 0 ]', i)]
    assert "delta-pass.sh" in branch, "the bound is reached and the delta proof is not asked"
    assert "promote-stg.sh" in branch, "the delta passes and the tip is not promoted"
    assert 'bounded "$left"' in branch, "the fallback spends no bound of its own and the attempt outruns fifteen minutes"
