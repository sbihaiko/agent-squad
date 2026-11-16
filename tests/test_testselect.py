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


def test_a_test_that_imports_from_a_selected_test_selects_that_test_too(tmp_path):
    """#2035: a fixture module (test A) loads `squad.gamma`, and a test (test B) imports its fixtures from A. A change to
    `squad.gamma` selects A, so B runs with it: the selection follows the imports of every selected test, transitively."""
    repo = tip(tmp_path, {"squad/gamma.py": "VALUE = 4\n"}, extra={
        "tests/test_flow.py": "from squad import gamma\nFIXTURE = gamma.VALUE\n",
        "tests/test_survival.py": "from tests.test_flow import FIXTURE\n",
    })
    assert select(repo) == ["tests/test_flow.py", "tests/test_gamma.py", "tests/test_survival.py"]


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
    ("contrib/squad-goal/scripts/check.sh", "check.sh"),
    ("contrib/squad-goal/scripts/check.sh", "scripts/check.sh"),
    ("squad/prompts/review.md", "squad/prompts/review.md"),
    ("docs/squad/workflows/default.yaml", "default.yaml"),
    ("CLAUDE.md", "CLAUDE.md"),
    ("docs/adr/0018-failing-gate-is-a-defect.md", "0018-failing-gate-is-a-defect.md"),
    (".claude/skills/tdd/SKILL.md", "SKILL.md"),
])
def test_a_changed_file_selects_each_test_that_names_it_by_path_or_basename(tmp_path, changed, named):
    repo = tip(
        tmp_path,
        {changed: "changed\n"},
        extra={changed: "old\n", "tests/test_names.py": f'NAMED = "{named}"\n', "tests/test_other.py": "X = 1\n"},
    )
    assert select(repo) == ["tests/test_names.py"]


@pytest.mark.parametrize("named", ["contrib/squad-goal/scripts", "contrib"])
def test_a_directory_literal_alone_does_not_select_a_test_under_it(tmp_path, named):
    """A test that names a directory has many scripts under it; naming the directory does not name this one (#1784)."""
    path = "contrib/squad-goal/scripts/check.sh"
    repo = tip(
        tmp_path,
        {path: "changed\n"},
        extra={path: "old\n", "tests/test_names.py": f'NAMED = "{named}"\n', "tests/test_other.py": "X = 1\n"},
    )
    lines = select(repo)
    assert len(lines) == 1 and lines[0].startswith("all: ") and path in lines[0], lines


def test_a_directory_literal_joined_to_another_script_does_not_select_the_changed_one(tmp_path):
    """The shape of the join the rule targets: the base of a path join names a directory, and the test names a sibling (#1784)."""
    joins = (
        "from pathlib import Path\n"
        "ROOT = Path(__file__).resolve().parent.parent\n"
        "OTHER = ROOT / 'contrib' / 'squad-goal' / 'scripts' / 'other.sh'\n"
    )
    repo = tip(
        tmp_path,
        {"contrib/squad-goal/scripts/check.sh": "changed\n"},
        extra={
            "contrib/squad-goal/scripts/check.sh": "old\n",
            "tests/test_joins.py": joins,
            "tests/test_names.py": 'NAMED = "check.sh"\n',
        },
    )
    assert select(repo) == ["tests/test_names.py"]


def test_a_directory_literal_and_the_basename_together_select_the_test(tmp_path):
    names = "DIR = 'contrib/squad-goal/scripts'\nNAME = 'check.sh'\n"
    repo = tip(
        tmp_path,
        {"contrib/squad-goal/scripts/check.sh": "changed\n"},
        extra={"contrib/squad-goal/scripts/check.sh": "old\n", "tests/test_names.py": names, "tests/test_other.py": "X = 1\n"},
    )
    assert select(repo) == ["tests/test_names.py"]


@pytest.mark.parametrize("lister", [
    "(ROOT / 'contrib' / 'squad-goal' / 'scripts').glob('*.sh')",
    "(ROOT / 'contrib').rglob('*.sh')",
    "(ROOT / 'contrib' / 'squad-goal' / 'scripts').iterdir()",
    "os.listdir(ROOT / 'contrib' / 'squad-goal' / 'scripts')",
    "os.scandir(ROOT / 'contrib' / 'squad-goal' / 'scripts')",
    "os.walk(ROOT / 'contrib')",
])
def test_a_test_that_lists_the_directory_selects_a_script_under_it(tmp_path, lister):
    lists = (
        "import os\n"
        "from pathlib import Path\n"
        "ROOT = Path(__file__).resolve().parent.parent\n"
        f"FOUND = {lister}\n"
    )
    repo = tip(
        tmp_path,
        {"contrib/squad-goal/scripts/check.sh": "changed\n"},
        extra={"contrib/squad-goal/scripts/check.sh": "old\n", "tests/test_lists.py": lists, "tests/test_other.py": "X = 1\n"},
    )
    assert select(repo) == ["tests/test_lists.py"]


def test_a_test_that_lists_a_sibling_directory_does_not_select_the_changed_script(tmp_path):
    lists = (
        "from pathlib import Path\n"
        "ROOT = Path(__file__).resolve().parent.parent\n"
        "FOUND = list((ROOT / 'contrib' / 'squad-goal' / 'other').glob('*.sh'))\n"
    )
    repo = tip(
        tmp_path,
        {"contrib/squad-goal/scripts/check.sh": "changed\n"},
        extra={"contrib/squad-goal/scripts/check.sh": "old\n", "tests/test_lists.py": lists, "tests/test_names.py": 'NAMED = "check.sh"\n'},
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


def test_a_module_that_loads_a_script_by_a_literal_path_never_asks_for_the_battery(tmp_path):
    """#1878: `squad/runners/dispatch.py` loads `contrib/squad-goal/scripts/issue_ledger.py` by a path literal, and that script
    imports nothing from `squad`. The target is scanned like any other module, so the static edge to `squad.alpha` is a real
    edge and the answer is the loader's own test, never the full battery."""
    loader = (
        "import importlib.util\n"
        "import pathlib\n"
        "from squad import alpha\n"
        "\n"
        "def helper():\n"
        "    p = pathlib.Path(__file__).parents[1] / 'scripts' / 'helper.py'\n"
        "    return importlib.util.spec_from_file_location('helper', p)\n"
    )
    repo = tip(tmp_path, {"squad/alpha.py": "VALUE = 2\n"}, extra={
        "squad/loader.py": loader,
        "scripts/helper.py": "VALUE = 9\n",
        "tests/test_loader.py": "from squad import loader\n",
    })
    assert select(repo) == ["tests/test_alpha.py", "tests/test_beta.py", "tests/test_loader.py"]


def test_a_module_whose_dynamic_target_names_no_existing_file_still_answers_all(tmp_path):
    """A load the selector cannot resolve is still an unknown edge: the module could reach any module, so the answer is the
    full battery and never a narrower list."""
    loader = (
        "import importlib\n"
        "from squad import alpha\n"
        "\n"
        "def load(name):\n"
        "    return importlib.import_module('squad.' + name)\n"
    )
    repo = tip(tmp_path, {"squad/alpha.py": "VALUE = 2\n"}, extra={
        "squad/loader.py": loader,
        "tests/test_loader.py": "from squad import loader\n",
    })
    assert select(repo)[0].startswith("all:")


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


SHARD_GUARD_BASE = {"tests/test_tooling.py": "def test_every_test_file_is_in_exactly_one_full_suite_shard():\n    pass\n"}


def test_a_delta_that_adds_a_test_file_selects_the_shard_guard(tmp_path):
    repo = tip(tmp_path, {"tests/test_new.py": "X = 1\n"}, extra=SHARD_GUARD_BASE)
    assert select(repo) == ["tests/test_new.py", "tests/test_tooling.py"]


def test_a_delta_that_deletes_a_test_file_selects_the_shard_guard(tmp_path):
    repo = tip(tmp_path, {"tests/test_gamma.py": None}, extra=SHARD_GUARD_BASE)
    assert select(repo) == ["tests/test_tooling.py"]


def test_a_delta_that_only_edits_a_test_file_does_not_select_the_shard_guard(tmp_path):
    repo = tip(tmp_path, {"tests/test_alpha.py": "from squad import alpha\nX = 1\n"}, extra=SHARD_GUARD_BASE)
    assert select(repo) == ["tests/test_alpha.py"]


def test_a_delta_that_edits_the_shard_map_selects_the_shard_guard_not_the_full_battery(tmp_path):
    """#1870: every card that adds a test file edits `tests/ci_shards.py` to give it a shard line, so the selector answered
    `all` for a required edit and spent a full battery on it. The guard that reads the map is the only test the edit reaches."""
    extra = {**SHARD_GUARD_BASE, "tests/ci_shards.py": "SHARDS = {'test_alpha.py': 'a'}\n"}
    repo = tip(tmp_path, {"tests/ci_shards.py": "SHARDS = {'test_alpha.py': 'a', 'test_beta.py': 'b'}\n"}, extra=extra)
    assert select(repo) == ["tests/test_tooling.py"]


def test_an_edited_shard_map_beside_a_changed_module_selects_the_guard_and_the_module_importers(tmp_path):
    extra = {**SHARD_GUARD_BASE, "tests/ci_shards.py": "SHARDS = {'test_alpha.py': 'a'}\n"}
    repo = tip(tmp_path, {"tests/ci_shards.py": "SHARDS = {'test_alpha.py': 'a', 'test_beta.py': 'b'}\n",
                          "squad/alpha.py": "VALUE = 2\n"}, extra=extra)
    assert sorted(select(repo)) == ["tests/test_alpha.py", "tests/test_beta.py", "tests/test_tooling.py"]


def test_an_edited_helper_answers_the_tests_that_import_it(tmp_path):
    """#1883: a helper under `tests/` answered `all` for any delta that touched it, though only the tests importing it reach
    it. Two gates in 3 h ran the full battery for `tests/launchdeps.py`."""
    repo = tip(
        tmp_path,
        {"tests/launchdeps.py": "DEPS = ('a', 'b')\n"},
        extra={"tests/launchdeps.py": "DEPS = ('a',)\n", "tests/test_uses_helper.py": "from tests import launchdeps\n"},
    )
    assert select(repo) == ["tests/test_uses_helper.py"]


def test_an_edited_helper_no_test_imports_still_answers_all(tmp_path):
    """A helper nothing imports — `conftest.py`, a fake application — is a reach the selector cannot see, so it answers the
    full battery and never a narrower list (ADR-0169)."""
    repo = tip(
        tmp_path,
        {"tests/fake_app.py": "APP = 2\n"},
        extra={"tests/fake_app.py": "APP = 1\n"},
    )
    assert select(repo)[0].startswith("all:")


MEASURED_LINE_GUARD_BASE = {
    "tests/test_shell_suite_runner.py": "def test_every_suite_has_a_measured_line_so_none_lands_unmeasured():\n    pass\n",
}


def test_a_delta_that_adds_a_shell_suite_selects_the_measured_line_guard(tmp_path):
    """The measured-line guard reads every `test-*.sh` beside the scripts and fails a suite with no line in
    tests/shell_suites_seconds.txt, so a delta that adds one reaches it."""
    repo = tip(
        tmp_path,
        {"contrib/squad-goal/scripts/test-new-suite.sh": "true\n"},
        extra=MEASURED_LINE_GUARD_BASE,
    )
    assert select(repo) == ["tests/test_shell_suite_runner.py"]


def test_a_delta_that_only_edits_a_shell_suite_does_not_select_the_measured_line_guard(tmp_path):
    """An edited suite keeps the line it already has, so only the test that names it is the delta."""
    suite = "contrib/squad-goal/scripts/test-alpha.sh"
    repo = tip(
        tmp_path,
        {suite: "true\necho alpha\n"},
        extra={**MEASURED_LINE_GUARD_BASE, suite: "true\n", "tests/test_namer.py": "SUITE = 'test-alpha.sh'\n"},
    )
    assert select(repo) == ["tests/test_namer.py"]
