"""The test selector answers a test list or `all` from the diff between a base ref and the tip, by imports only."""
import ast
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tests.gitrepo import make_repo, sh

ROOT = Path(__file__).resolve().parent.parent

BASE = {
    "squad/__init__.py": "",
    "squad/alpha.py": "VALUE = 1\n",
    "squad/beta.py": "from squad import alpha\n",
    "squad/gamma.py": "VALUE = 3\n",
    "squad/testselect.py": "",
    "squad/testselect_inert.txt": "",
    "tests/__init__.py": "",
    "tests/test_alpha.py": "from squad import alpha\n",
    "tests/test_beta.py": "import squad.beta\n",
    "tests/test_gamma.py": "from squad.gamma import VALUE\n",
    "tests/conftest.py": "",
    "tests/gitrepo.py": "",
    "pyproject.toml": "[project]\n",
    "uv.lock": "",
    "config/agent-defaults.yaml": "x: 1\n",
}


def tip(tmp_path, edits, extra=None):
    """A repository whose base commit holds BASE plus `extra`, and whose tip commit applies `edits` (None deletes)."""
    repo = make_repo(tmp_path / "repo")
    files = {**BASE, **(extra or {})}
    for path, text in files.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text)
    sh(repo, "git", "add", "-A")
    sh(repo, "git", "commit", "-q", "-m", "base")
    sh(repo, "git", "checkout", "-q", "-b", "tip")
    for path, text in edits.items():
        if text is None:
            (repo / path).unlink()
        else:
            (repo / path).parent.mkdir(parents=True, exist_ok=True)
            (repo / path).write_text(text)
    sh(repo, "git", "add", "-A")
    sh(repo, "git", "commit", "-q", "--allow-empty", "-m", "tip")
    return repo


def select(repo, base="main", stage=None):
    argv = [sys.executable, "-m", "squad.testselect", "--base", base, "--worktree", str(repo)]
    if stage is not None:
        argv += ["--stage", stage]
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    done = subprocess.run(argv, cwd=ROOT, env=env, capture_output=True, text=True, check=True)
    return done.stdout.splitlines()


def test_a_changed_squad_module_selects_its_importers_directly_and_through_squad(tmp_path):
    repo = tip(tmp_path, {"squad/alpha.py": "VALUE = 2\n"})
    assert select(repo) == ["tests/test_alpha.py", "tests/test_beta.py"]


def test_a_changed_test_file_selects_itself(tmp_path):
    repo = tip(tmp_path, {"tests/test_gamma.py": "from squad.gamma import VALUE\nX = 1\n"})
    assert select(repo) == ["tests/test_gamma.py"]


def none_reason(lines):
    """The reason of a `none:` answer: the delta selects no test, and the line says why (#1249). None when it is not one."""
    return lines[0].removeprefix("none: ") if len(lines) == 1 and lines[0].startswith("none: ") else None


def test_a_deleted_test_file_answers_all_because_pytest_cannot_run_a_missing_path(tmp_path):
    """A path absent at the tip made the gate's pytest collect nothing (exit 5). An empty selection proves nothing (#1249),
    so a delta whose every test the tip deleted falls through to the full battery and names the deleted path."""
    repo = tip(tmp_path, {"tests/test_gamma.py": None})
    lines = select(repo)
    assert len(lines) == 1 and lines[0].startswith("all: ") and "tests/test_gamma.py" in lines[0]


@pytest.mark.parametrize("dirt", ["tracked", "untracked"])
def test_an_uncommitted_change_answers_all_and_names_the_files(tmp_path, dirt):
    """(#1249) The selector reads the committed diff, so a change the tree carries outside it cannot be placed. It never
    selects nothing for it: the answer is the full battery, and the line names the uncommitted files."""
    repo = tip(tmp_path, {})
    path = "squad/alpha.py" if dirt == "tracked" else "squad/stray.py"
    (repo / path).write_text("VALUE = 9\n")
    lines = select(repo)
    assert len(lines) == 1 and lines[0].startswith("all: ") and "uncommitted" in lines[0] and path in lines[0], lines


def test_an_uncommitted_change_beside_a_covered_commit_still_answers_all(tmp_path):
    repo = tip(tmp_path, {"squad/alpha.py": "VALUE = 2\n"})
    (repo / "squad/gamma.py").write_text("VALUE = 4\n")
    lines = select(repo)
    assert len(lines) == 1 and lines[0].startswith("all: ") and "squad/gamma.py" in lines[0], lines


def test_a_deleted_squad_module_selects_the_tests_that_still_import_it(tmp_path):
    repo = tip(tmp_path, {"squad/alpha.py": None})
    assert select(repo) == ["tests/test_alpha.py", "tests/test_beta.py"]


def test_a_renamed_test_file_selects_only_the_new_path(tmp_path):
    repo = tip(tmp_path, {"tests/test_gamma.py": None, "tests/test_delta.py": "from squad.gamma import VALUE\n"})
    assert select(repo) == ["tests/test_delta.py"]


@pytest.mark.parametrize("path", [
    "tests/conftest.py",
    "tests/gitrepo.py",
    "pyproject.toml",
    "uv.lock",
    "config/agent-defaults.yaml",
    "squad/testselect.py",
])
def test_a_shared_file_answers_all_with_a_reason(tmp_path, path):
    repo = tip(tmp_path, {path: "changed\n"})
    lines = select(repo)
    assert len(lines) == 1 and re.fullmatch(r"all: \S.*", lines[0]) and path in lines[0]


def test_a_dynamic_importer_on_the_edge_to_a_changed_module_answers_all(tmp_path):
    loader = (
        "import importlib\n"
        "from squad import alpha\n"
        "\n"
        "def load(name):\n"
        "    return importlib.import_module('squad.' + name)\n"
    )
    repo = tip(tmp_path, {"squad/alpha.py": "VALUE = 2\n"}, extra={"squad/loader.py": loader})
    lines = select(repo)
    assert len(lines) == 1 and lines[0].startswith("all: ") and "squad/loader.py" in lines[0]


def test_a_dynamic_import_off_the_edge_to_a_changed_module_does_not_answer_all(tmp_path):
    dyn = (
        "import importlib\n"
        "\n"
        "def load(name):\n"
        "    return importlib.import_module('squad.' + name)\n"
    )
    repo = tip(tmp_path, {"squad/alpha.py": "VALUE = 2\n"}, extra={"squad/dyn.py": dyn})
    assert select(repo) == ["tests/test_alpha.py", "tests/test_beta.py"]


def test_a_literal_dynamic_import_is_a_static_edge(tmp_path):
    literal = "import importlib\nMOD = importlib.import_module('squad.gamma')\n"
    repo = tip(tmp_path, {"squad/gamma.py": "VALUE = 4\n"}, extra={"tests/test_literal.py": literal})
    assert select(repo) == ["tests/test_gamma.py", "tests/test_literal.py"]


@pytest.mark.parametrize("path, text", [
    ("squad/orphan.py", "VALUE = 9\n"),
    ("docs/notes.md", "notes\n"),
    ("contrib/squad-goal/scripts/orphan.sh", "echo\n"),
])
def test_a_changed_file_no_import_edge_reaches_answers_all(tmp_path, path, text):
    repo = tip(tmp_path, {path: text})
    lines = select(repo)
    assert len(lines) == 1 and lines[0].startswith("all: ") and path in lines[0]


def test_a_base_that_does_not_resolve_answers_all_never_an_empty_list(tmp_path):
    repo = tip(tmp_path, {"squad/alpha.py": "VALUE = 2\n"})
    lines = select(repo, base="no-such-ref")
    assert len(lines) == 1 and lines[0].startswith("all: ") and "no-such-ref" in lines[0]


@pytest.mark.parametrize("stage", ["fast", "wave-entry", "wave-tip", "prepush"])
def test_an_intermediate_stage_selects_the_delta_for_a_covered_change(tmp_path, stage):
    """The stage that runs on every version of a tree names the delta: the test files the change reaches, never the battery."""
    repo = tip(tmp_path, {"squad/alpha.py": "VALUE = 2\n"})
    assert select(repo, stage=stage) == ["tests/test_alpha.py", "tests/test_beta.py"]


@pytest.mark.parametrize("stage", ["landing", "promotion", "nightly"])
def test_a_full_stage_runs_the_full_battery_whatever_the_delta_says(tmp_path, stage):
    """The landing, the promotion proof and the nightly keep the battery: a covered change is no reason to narrow them."""
    repo = tip(tmp_path, {"squad/alpha.py": "VALUE = 2\n"})
    assert select(repo, stage=stage) == [f"all: {stage} runs the full battery"]


def test_an_unknown_stage_falls_back_to_the_full_battery_and_never_narrows(tmp_path):
    """A stage the selector does not know is full: a typo costs the battery, never a narrower gate (ADR-0018)."""
    repo = tip(tmp_path, {"squad/alpha.py": "VALUE = 2\n"})
    assert select(repo, stage="typo") == ["all: typo runs the full battery"]


def test_an_uncovered_path_at_an_intermediate_stage_states_why_the_delta_refused(tmp_path):
    """The delta cannot cover a path no test reaches: the answer is the full battery, and the line says the reason."""
    repo = tip(tmp_path, {"docs/notes.md": "notes\n"})
    lines = select(repo, stage="wave-entry")
    assert len(lines) == 1 and lines[0].startswith("all: ") and "docs/notes.md" in lines[0]


def test_no_change_selects_no_test_and_says_why(tmp_path):
    repo = tip(tmp_path, {})
    assert none_reason(select(repo)) == "the tip changes no file against main"


@pytest.mark.parametrize("changed, named", [
    ("contrib/squad-goal/scripts/check.sh", "contrib/squad-goal/scripts/check.sh"),
    ("contrib/squad-goal/scripts/check.sh", "contrib/squad-goal/scripts"),
    ("squad/prompts/review.md", "squad/prompts"),
    ("docs/squad/workflows/default.yaml", "docs/squad/workflows/default.yaml"),
    ("CLAUDE.md", "CLAUDE.md"),
    ("docs/adr/0018-failing-gate-is-a-defect.md", "docs/adr"),
    (".claude/skills/tdd/SKILL.md", ".claude/skills/tdd"),
])
def test_a_changed_file_selects_each_test_that_names_it_or_a_directory_above_it(tmp_path, changed, named):
    repo = tip(
        tmp_path,
        {changed: "changed\n"},
        extra={changed: "old\n", "tests/test_names.py": f'NAMED = "{named}"\n', "tests/test_other.py": "X = 1\n"},
    )
    assert select(repo) == ["tests/test_names.py"]


def test_a_named_directory_does_not_match_a_sibling_that_shares_its_prefix(tmp_path):
    path = "contrib/squad-goal/scripts-old/check.sh"
    repo = tip(
        tmp_path,
        {path: "changed\n"},
        extra={path: "old\n", "tests/test_names.py": 'NAMED = "contrib/squad-goal/scripts"\n'},
    )
    lines = select(repo)
    assert len(lines) == 1 and lines[0].startswith("all: ") and path in lines[0]


def test_a_path_literal_adds_to_the_import_edges_of_a_changed_module(tmp_path):
    repo = tip(tmp_path, {"squad/alpha.py": "VALUE = 2\n"}, extra={"tests/test_names.py": 'NAMED = "squad/alpha.py"\n'})
    assert select(repo) == ["tests/test_alpha.py", "tests/test_beta.py", "tests/test_names.py"]


def test_a_deleted_path_a_test_still_names_selects_that_test(tmp_path):
    path = "contrib/squad-goal/scripts/retired.sh"
    repo = tip(
        tmp_path,
        {path: None},
        extra={path: "old\n", "tests/test_retired.py": f'SCRIPT = "{path}"\n'},
    )
    assert select(repo) == ["tests/test_retired.py"]


INERT = "squad/testselect_inert.txt"
INERT_PATH = "docs/agents/triage-labels.md"


def test_a_changed_inert_path_selects_nothing(tmp_path):
    repo = tip(tmp_path, {INERT_PATH: "changed\n"}, extra={INERT_PATH: "old\n", INERT: f"{INERT_PATH}\n"})
    assert none_reason(select(repo)) == f"every changed file is inert: {INERT_PATH}"


def test_a_changed_path_on_the_shipped_inert_list_selects_nothing(tmp_path):
    shipped = (ROOT / INERT).read_text()
    path = next(line for line in shipped.splitlines() if line and not line.startswith("#"))
    repo = tip(tmp_path, {path: "changed\n"}, extra={path: "old\n", INERT: shipped})
    assert none_reason(select(repo)) == f"every changed file is inert: {path}"


def test_an_inert_change_adds_nothing_to_the_tests_a_mapped_change_selects(tmp_path):
    repo = tip(
        tmp_path,
        {INERT_PATH: "changed\n", "squad/alpha.py": "VALUE = 2\n"},
        extra={INERT_PATH: "old\n", INERT: f"{INERT_PATH}\n"},
    )
    assert select(repo) == ["tests/test_alpha.py", "tests/test_beta.py"]


def test_a_change_to_the_inert_list_answers_all_with_the_path(tmp_path):
    repo = tip(tmp_path, {INERT: f"{INERT_PATH}\n"}, extra={INERT: ""})
    lines = select(repo)
    assert len(lines) == 1 and lines[0].startswith("all: ") and INERT in lines[0]


@pytest.mark.heavy   # parses every test file, and the runner's load can push the scan past the 2 s line
def test_the_inert_list_names_no_path_a_test_reads():
    entries = [
        line.strip() for line in (ROOT / INERT).read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    literals = set()
    for path in (ROOT / "tests").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                literals.add(node.value.strip().removeprefix("./").rstrip("/"))
    assert entries
    for entry in entries:
        base = entry.rsplit("/", 1)[-1]
        read = sorted(lit for lit in literals if lit == entry or entry.startswith(lit + "/") or lit == base)
        assert not read, f"{entry} is named by a test: {read}"


def test_a_changed_shell_suite_seconds_file_selects_the_tests_that_import_its_reader(tmp_path):
    repo = tip(
        tmp_path,
        {"tests/shell_suites_seconds.txt": "suite 2\n"},
        extra={
            "tests/shell_suites_seconds.txt": "suite 1\n",
            "tests/shell_suites.py": "SECONDS_LIST = 'x'\n",
            "tests/test_reader.py": "from tests import shell_suites\n",
            "tests/test_other.py": "X = 1\n",
        },
    )
    assert select(repo) == ["tests/test_reader.py"]


def test_a_dynamic_test_with_no_import_edge_is_selected_by_its_declared_path(tmp_path):
    """The DEPENDS list is a selection: a test that loads its modules by path is picked up from the path it declares."""
    loader = (
        "import importlib\n"
        "\n"
        "DEPENDS = ('squad/gamma.py',)\n"
        "\n"
        "def load(name):\n"
        "    return importlib.import_module(name)\n"
    )
    repo = tip(tmp_path, {"squad/gamma.py": "VALUE = 4\n"}, extra={"tests/test_dyn.py": loader})
    assert select(repo) == ["tests/test_dyn.py", "tests/test_gamma.py"]


def test_a_dynamic_test_that_declares_the_changed_path_answers_its_list_not_all(tmp_path):
    loader = (
        "import importlib\n"
        "from squad import beta\n"
        "\n"
        "DEPENDS = ('squad/alpha.py',)\n"
        "\n"
        "def load(name):\n"
        "    return importlib.import_module('squad.' + name)\n"
    )
    repo = tip(tmp_path, {"squad/alpha.py": "VALUE = 2\n"}, extra={"tests/test_dyn.py": loader})
    assert select(repo) == ["tests/test_alpha.py", "tests/test_beta.py", "tests/test_dyn.py"]


def test_a_dynamic_test_on_an_undeclared_edge_is_selected_and_never_asks_for_the_battery(tmp_path):
    """A test on the edge is reached, so it is selected; its own dynamic load hides no importer whose selection a wider answer
    would protect. A declaration for another path therefore costs this one test nothing: the edge answers the tests, never the
    full battery (ADR-0169)."""
    loader = (
        "import importlib\n"
        "from squad import beta\n"
        "\n"
        "DEPENDS = ('squad/gamma.py',)\n"
        "\n"
        "def load(name):\n"
        "    return importlib.import_module('squad.' + name)\n"
    )
    repo = tip(tmp_path, {"squad/alpha.py": "VALUE = 2\n"}, extra={"tests/test_dyn.py": loader})
    assert select(repo) == ["tests/test_alpha.py", "tests/test_beta.py", "tests/test_dyn.py"]


def test_the_measured_seconds_file_selects_its_readers_though_one_loads_a_script_by_path(tmp_path):
    """The shape a hand gate meets: a script change edits the measured line of its suite, the selector maps that data file to
    its reader, and a test importing the reader also loads a script by a computed path. The reader's tests are the delta; their
    own dynamic load never widens the answer to the full battery (ADR-0169)."""
    reader = "def run(p):\n    return None\n"
    dynamic_reader = (
        "import importlib.util\n"
        "from tests import shell_suites\n"
        "\n"
        "SCRIPTS = 'contrib/squad-goal/scripts'\n"
        "SPEC = importlib.util.spec_from_file_location('m', SCRIPTS + '/watchd.sh')\n"
    )
    repo = tip(
        tmp_path,
        {"tests/shell_suites_seconds.txt": "suite 1\nsuite 2\n"},
        extra={
            "tests/shell_suites_seconds.txt": "suite 1\n",
            "tests/shell_suites.py": reader,
            "tests/test_reader.py": "from tests import shell_suites\n",
            "tests/test_dynamic.py": dynamic_reader,
        },
    )
    assert select(repo) == ["tests/test_dynamic.py", "tests/test_reader.py"]
