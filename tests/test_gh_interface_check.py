"""No read and no write bypasses the loop's one interface, and a check says so (#8, ADR-0277).

The interface is `contrib/squad-goal/scripts/ghread.py` -- one module that answers in exactly three outcomes and never
hands a refusal back where an answer belongs -- plus the shell door every loop script reaches it through
(`contrib/squad-goal/scripts/ghcall.sh`: `gh_api`, `gh_send`, `gh_get`, `gh_graphql`). A script that keeps its own call
site recreates the failure the interface exists to prevent: a refused read looks like an answer, and a refused write
looks like success -- a moved card and a closed issue reported as done while GitHub refused both.

`check-gh-interface.sh` is the guard that refuses a live script which reaches GitHub directly. These cases drive it
against fixture trees, because the rule has to be provable rather than asserted: the read case names the script and the
route, the write case fails exactly as the read case does, and the live tree's own case is the acceptance criterion
that every read in the loop's scripts goes through the interface.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / "contrib/squad-goal/scripts/check-gh-interface.sh"

SCRIPTS = "contrib/squad-goal/scripts"


def tree(tmp_path: Path, files: dict[str, str]) -> Path:
    """A fixture repository: each entry is a file under the loop's script directory, named as it is on disk."""
    for name, body in files.items():
        f = tmp_path / SCRIPTS / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(body)
    return tmp_path


def run(target: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(CHECK), str(target)], capture_output=True, text=True, check=False)


def test_a_direct_read_fails_naming_the_script_and_the_route(tmp_path: Path) -> None:
    """A live script that reads GitHub with its own `gh api` is refused, and the refusal names the script and the route."""
    target = tree(
        tmp_path,
        {
            "reader.sh": "#!/bin/bash\no=$(gh api \"repos/o/r/pulls?state=open&per_page=100\" -q '.[].number') || exit 1\n",
        },
    )
    p = run(target)
    assert p.returncode == 1, f"a direct read must be refused: rc={p.returncode} {p.stdout}{p.stderr}"
    assert "reader.sh" in p.stdout, f"the refusal must name the script: {p.stdout}"
    assert "repos/o/r/pulls?state=open&per_page=100" in p.stdout, f"the refusal must name the route: {p.stdout}"
    assert "read" in p.stdout, f"the refusal must say which reach it was: {p.stdout}"


def test_a_direct_write_fails_naming_the_script_and_the_route(tmp_path: Path) -> None:
    """The revision's half: a card moved or an issue closed behind the interface's back is refused exactly as a read is."""
    target = tree(
        tmp_path,
        {
            "writer.sh": "#!/bin/bash\ngh api -X PATCH \"repos/o/r/issues/7\" -f state=closed >/dev/null 2>&1 && touch done\n",
        },
    )
    p = run(target)
    assert p.returncode == 1, f"a direct write must be refused: rc={p.returncode} {p.stdout}{p.stderr}"
    assert "writer.sh" in p.stdout, f"the refusal must name the script: {p.stdout}"
    assert "repos/o/r/issues/7" in p.stdout, f"the refusal must name the route: {p.stdout}"
    assert "write" in p.stdout, f"the refusal must say which reach it was: {p.stdout}"


def test_a_write_through_the_door_passes(tmp_path: Path) -> None:
    """The reachable ways are the doors: a caller that goes through them is clean."""
    target = tree(
        tmp_path,
        {
            "caller.sh": (
                "#!/bin/bash\n"
                '. "$(dirname "$0")/ghcall.sh"\n'
                "gh_api \"repos/o/r/pulls?state=open&per_page=100\" -q '.[].number'\n"
                "gh_send -X POST \"repos/o/r/issues/7/comments\" -F body=@-\n"
            ),
        },
    )
    p = run(target)
    assert p.returncode == 0, f"a caller through the door is clean: rc={p.returncode} {p.stdout}{p.stderr}"


def test_only_the_interface_itself_may_call_gh(tmp_path: Path) -> None:
    """The door and the module are where `gh` is called -- that is what an interface is -- so both are exempt by name."""
    target = tree(
        tmp_path,
        {
            "ghcall.sh": "#!/bin/bash\ngh_api() { gh api --include \"$1\"; }\n",
            "ghread.py": "import subprocess\nsubprocess.run(['gh', 'api', 'repos/o/r'])\n",
            "reader.sh": "#!/bin/bash\no=$(gh api repos/o/r/pulls?state=open)\n",
        },
    )
    p = run(target)
    assert p.returncode == 1, f"a live caller is still refused beside the exempt interface: rc={p.returncode} {p.stdout}"
    assert "reader.sh" in p.stdout, f"the caller must be named: {p.stdout}"
    assert "OFFENDS: ghcall.sh" not in p.stdout, f"the interface is exempt: {p.stdout}"
    assert "OFFENDS: ghread.py" not in p.stdout, f"the interface is exempt: {p.stdout}"


def test_a_comment_may_name_a_call_it_does_not_make(tmp_path: Path) -> None:
    """A line that names the words to say they are refused is not a call site; only code counts as a reach."""
    target = tree(
        tmp_path,
        {
            "doc.sh": "#!/bin/bash\n# every `gh api … 2>/dev/null` here used to treat a 403 body as an answer\ngh_api repos/o/r\n",
        },
    )
    p = run(target)
    assert p.returncode == 0, f"a comment is not a reach: rc={p.returncode} {p.stdout}{p.stderr}"


def test_a_test_spells_the_words_to_prove_they_are_refused(tmp_path: Path) -> None:
    """A suite's stub serves the argv `gh` gets; spelling a direct call there is how the refusal is proven."""
    target = tree(
        tmp_path,
        {
            "test-offender.sh": (
                "#!/bin/bash\n"
                "printf '#!/bin/bash\\ncase \"$*\" in *pulls?state=open*) echo \"[]\";; esac\\n' > \"$T/bin/gh\"\n"
                "gh api repos/o/r/pulls?state=open\n"
            ),
        },
    )
    p = run(target)
    assert p.returncode == 0, f"a test file is not a live script: rc={p.returncode} {p.stdout}{p.stderr}"


def test_a_script_over_its_migration_budget_is_refused(tmp_path: Path) -> None:
    """The budget is a ceiling, not an exemption: one reach past the line and the script is refused, route and all."""
    target = tree(
        tmp_path,
        {
            "reader.sh": (
                "#!/bin/bash\n"
                "o=$(gh api repos/o/r/pulls?state=open)\n"
                "gh api repos/o/r/issues?state=open\n"
            ),
            "gh-direct-calls.txt": "# the call sites still to move\nreader.sh 1\n",
        },
    )
    p = run(target)
    assert p.returncode == 1, f"a script over its budget is refused: rc={p.returncode} {p.stdout}{p.stderr}"
    assert "reader.sh" in p.stdout and "repos/o/r/issues?state=open" in p.stdout, f"naming it: {p.stdout}"


def test_a_script_below_its_budget_is_told_to_shrink_it(tmp_path: Path) -> None:
    """A migrated caller must not leave its old line standing: the check reports it. (Reported, not refused: a guard
    that reds the tree for a caller that improved is a guard nobody lands.)"""
    target = tree(
        tmp_path,
        {
            "caller.sh": "#!/bin/bash\n. \"$(dirname \"$0\")/ghcall.sh\"\ngh_api repos/o/r\n",
            "gh-direct-calls.txt": "caller.sh 2\n",
        },
    )
    p = run(target)
    assert p.returncode == 0, f"a migrated caller passes: rc={p.returncode} {p.stdout}{p.stderr}"
    assert "SHRINK: caller.sh" in p.stderr, f"and is told to lower its line: {p.stderr!r}"


def test_the_live_tree_is_within_its_migration_budget() -> None:
    """The acceptance criterion at HEAD, as far as the base allows it.

    Every read the loop's scripts make goes through the door in the sense this check can prove: no script reaches
    GitHub beyond its line in `gh-direct-calls.txt`, a script the file does not name may not reach it at all, and the
    file only shrinks. The ticket's own wording -- every reach through the interface, no grandfather list -- is what
    the file's last line going away means, and until then this case is the honest bound: it is green here, and it goes
    red the moment a script reads GitHub behind the interface's back.
    """
    p = run(ROOT)
    assert p.returncode == 0, f"the loop's scripts reach GitHub over their budget:\n{p.stdout}{p.stderr}"
