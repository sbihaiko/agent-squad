"""No loop script operates on a pull request, save the promotion: the work branch is the unit of work (#361, ADR-0145).

A loop script creates, reads or merges no pull request. The one exception is the consolidation of `dev` into `stg`, which
the owner ruled to be one pull request (#743): `promote-stg.sh` opens or reuses that pull request and merges it, and it is
the only loop script that names a pulls route. The pull request otherwise survives in one place only -- the page, which
the owner reads: `hub.py` (`OPEN_PR_QUERY`, `prs_from_graphql`), `board_columns.py` (the PR lane and the PR kind),
`hubmux.py` and `hubctl.sh`. Reading the `pull_request` field of an issue to skip a pull request in an issue list is not
a pull-request operation either, and neither is the word "pull request" in a sentence that says the path was removed.

This guard reads every other script the loop runs and fails on a pull-request *operation*: a `gh pr <verb>` call in
command position, a `repos/<owner>/<repo>/pulls` route, or a `pullRequests(` GraphQL connection. The door and its own
checkers (`ghcall.sh`, `ghread.py`, `ghthrottle.py`, `check-gh-interface.py`, `check-gh-interface.sh`,
`check-suite-env-hermetic.py`) speak routes and `gh pr` verbs as their vocabulary and are not loop scripts; a `test-*.sh`
beside the scripts is a suite, not the loop. `gh-direct-calls.txt` names no pull-request caller, and `SKILL.md`
describes the branch landing only.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "contrib" / "squad-goal" / "scripts"
SKILL = ROOT / "contrib" / "squad-goal" / "SKILL.md"
BUDGET = SCRIPTS / "gh-direct-calls.txt"

# The page (the owner reads it) and the door with its checkers (their vocabulary is routes and gh verbs). Not loop work.
# The page is one program: hub.py compiles its hub_theme_*.py modules into its own namespace (#2742), so the page set is
# hub.py and every theme module it loads.
PAGE = ({"hub.py"} | {p.name for p in SCRIPTS.glob("hub_theme_*.py")}) | {"board_columns.py", "hubmux.py", "hubctl.sh"}
DOOR = {"ghcall.sh", "ghread.py", "ghthrottle.py", "check-gh-interface.py", "check-gh-interface.sh", "check-suite-env-hermetic.py"}

# A pull-request operation. `gh pr <verb>` counts only in command position, so prose that names a verb is not a call.
GH_PR = re.compile(r"(?:^|[;|&()`${}!]|\b(?:then|do|else|elif)\s)gh\s+pr\s+[a-z]")
PULLS_ROUTE = re.compile(r"repos/\S*?/pulls\b")
PULLS_GRAPHQL = re.compile(r"pullRequests\s*\(")

# The scripts that only ever served the pull-request path, so the budget file names no pull-request caller.
PR_CALLERS = {"prland.sh", "revci.sh", "autopilot.sh", "autofix.sh", "autopanel.sh", "stack.sh", "speed.py", "retryfix.sh"}

# The one loop script that operates on a pull request, by the owner's rule (#743): the consolidation of `dev` into `stg`.
PROMOTION = {"promote-stg.sh"}


def loop_scripts() -> list[Path]:
    """Every script the loop runs: the .sh and .py beside the scripts, less the page, the door, its checkers and the suites."""
    picked = [p for p in sorted(SCRIPTS.glob("*.sh")) + sorted(SCRIPTS.glob("*.py")) if p.name not in PAGE | DOOR]
    return [p for p in picked if not p.name.startswith("test-")]


def offenders(only: set[str] | None = None) -> list[str]:
    """The pull-request operations in the loop scripts, less the promotion; `only` reads the promotion alone."""
    hits = []
    for p in loop_scripts():
        if p.name in PROMOTION and p.name not in (only or set()):
            continue
        text = p.read_text(errors="ignore")
        for m in GH_PR.finditer(text):
            hits.append(f"{p.name}: a `gh pr` call: {text[m.start():m.start() + 60].splitlines()[0].strip()!r}")
        for m in PULLS_ROUTE.finditer(text):
            hits.append(f"{p.name}: a pulls route: {text[m.start():m.start() + 60].splitlines()[0].strip()!r}")
        for m in PULLS_GRAPHQL.finditer(text):
            hits.append(f"{p.name}: a pullRequests( connection: {text[m.start():m.start() + 60].splitlines()[0].strip()!r}")
    return hits


def test_no_loop_script_operates_on_a_pull_request() -> None:
    hits = offenders()
    assert hits == [], "a loop script still operates on a pull request:\n" + "\n".join(hits)


def test_the_promotion_is_the_one_pull_request_path() -> None:
    """#743: `dev` reaches `stg` by one pull request, and it is the only pull request a loop script touches."""
    assert PROMOTION == {"promote-stg.sh"}
    hits = offenders(only=PROMOTION)
    assert hits, "promote-stg.sh no longer operates on the pull request: the guard would pass for the wrong reason"
    assert all(h.startswith("promote-stg.sh:") for h in hits)


def test_the_budget_file_names_no_pull_request_caller() -> None:
    named = {line.split()[0] for line in BUDGET.read_text().splitlines() if line.split() and not line.lstrip().startswith("#")}
    assert named & PR_CALLERS == set(), f"the budget file still names a pull-request caller: {sorted(named & PR_CALLERS)}"


def test_skill_md_describes_the_branch_landing_only() -> None:
    """No line describes the loop's work by a pull request. One line says that path was removed; the one pull request the loop opens is the promotion of `dev` into `stg` (ADR-0145 rule 3), and the skill must name it."""
    word = re.compile(r"\b(?:pull\s*request|pull-request|PR)\b", re.IGNORECASE)
    seen = [(i + 1, line) for i, line in enumerate(SKILL.read_text().splitlines()) if word.search(line)]
    assert seen, "SKILL.md should name the removal of the pull-request path once"

    def is_the_promotion(line: str) -> bool:
        return bool(re.search(r"promot", line, re.IGNORECASE)) and bool(re.search(r"\bstg\b", line))

    stray = [
        (i, line.strip())
        for i, line in seen
        if not re.search(r"remov|no longer|no pull request", line, re.IGNORECASE) and not is_the_promotion(line)
    ]
    assert stray == [], f"SKILL.md still describes the loop's work by a pull request: {stray}"
    assert any(is_the_promotion(line) for _, line in seen), (
        "SKILL.md must name the one pull request the loop opens: the promotion of `dev` into `stg`"
    )
