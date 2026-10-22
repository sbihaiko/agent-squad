"""The test selector answers a test list or `all` from the diff between a base ref and the tip, by imports only."""
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


def select(repo, base="main"):
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    done = subprocess.run(
        [sys.executable, "-m", "squad.testselect", "--base", base, "--worktree", str(repo)],
        cwd=ROOT, env=env, capture_output=True, text=True, check=True,
    )
    return done.stdout.splitlines()


def test_a_changed_squad_module_selects_its_importers_directly_and_through_squad(tmp_path):
    repo = tip(tmp_path, {"squad/alpha.py": "VALUE = 2\n"})
    assert select(repo) == ["tests/test_alpha.py", "tests/test_beta.py"]


def test_a_changed_test_file_selects_itself(tmp_path):
    repo = tip(tmp_path, {"tests/test_gamma.py": "from squad.gamma import VALUE\nX = 1\n"})
    assert select(repo) == ["tests/test_gamma.py"]


def test_a_deleted_test_file_is_selected_as_changed(tmp_path):
    repo = tip(tmp_path, {"tests/test_gamma.py": None})
    assert select(repo) == ["tests/test_gamma.py"]


def test_a_deleted_squad_module_selects_the_tests_that_still_import_it(tmp_path):
    repo = tip(tmp_path, {"squad/alpha.py": None})
    assert select(repo) == ["tests/test_alpha.py", "tests/test_beta.py"]


def test_a_renamed_test_file_selects_the_old_and_the_new_path(tmp_path):
    repo = tip(tmp_path, {"tests/test_gamma.py": None, "tests/test_delta.py": "from squad.gamma import VALUE\n"})
    assert select(repo) == ["tests/test_delta.py", "tests/test_gamma.py"]


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
])
def test_a_changed_file_no_import_edge_reaches_answers_all(tmp_path, path, text):
    repo = tip(tmp_path, {path: text})
    lines = select(repo)
    assert len(lines) == 1 and lines[0].startswith("all: ") and path in lines[0]


def test_a_base_that_does_not_resolve_answers_all_never_an_empty_list(tmp_path):
    repo = tip(tmp_path, {"squad/alpha.py": "VALUE = 2\n"})
    lines = select(repo, base="no-such-ref")
    assert len(lines) == 1 and lines[0].startswith("all: ") and "no-such-ref" in lines[0]


def test_no_change_selects_no_test(tmp_path):
    repo = tip(tmp_path, {})
    assert select(repo) == []
