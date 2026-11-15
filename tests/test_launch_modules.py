"""A test sandbox that runs launch.py provides every module it loads (#1832, #1862).

launch.py loads a sibling module by path with `spec_from_file_location(..., here / "<name>.py")`. The pytest cases that run
it in a throwaway folder copy them, and a missing one is a FileNotFoundError at import time, not a failure of what the case
checks. It also loads `squad/<name>.py` from a root (`$SG_SQUAD` first), and a sandbox that hosts no such file is refused
before the run starts. This guard reads both loads out of launch.py's syntax tree, so a new one fails here before it fails
three suites on every tier.

The read follows the path expression, not the line: a `/` join's literal segments are the file's path, and a name bound to
such a join is resolved, so a load spelled through a variable is read the same as one written inline.
"""
import ast
import importlib
import os
import pathlib
import sys
from pathlib import Path

from tests.launchdeps import LAUNCH_MODULES, SQUAD_MODULES, install_squad_modules

SCRIPTS = Path(__file__).resolve().parents[1] / "contrib/squad-goal/scripts"


def _join(node):
    """The literal segments and the base of a `Path / "a" / "b"` chain, or None when a step is not a literal."""
    segments = []
    while isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        right = node.right
        if not (isinstance(right, ast.Constant) and isinstance(right.value, str)):
            return None
        segments.append(right.value)
        node = node.left
    segments.reverse()
    return segments, node


def _loads(src):
    """(beside-itself modules, `squad/` modules) that every `spec_from_file_location` call in `src` takes."""
    tree = ast.parse(src)
    bound = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, (ast.Name, ast.BinOp, ast.Constant)):
            bound.update({t.id: node.value for t in node.targets if isinstance(t, ast.Name)})
    launch, squad = set(), set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or len(node.args) < 2:
            continue
        if not (isinstance(node.func, ast.Attribute) and node.func.attr == "spec_from_file_location"):
            continue
        path = node.args[1]
        if isinstance(path, ast.Name) and path.id in bound:
            path = bound[path.id]
        joined = _join(path)
        if joined is None:
            continue
        segments, base = joined
        if segments[:1] == ["squad"] and len(segments) > 1:
            squad.update(segments[1:])
        elif isinstance(base, ast.Name) and base.id == "here":
            launch.update(segments)
    return launch, squad


def test_the_sandbox_list_holds_every_module_launch_loads():
    loaded, _ = _loads((SCRIPTS / "launch.py").read_text())
    assert loaded, "launch.py loads no sibling module; the read is broken, not the list"
    assert loaded == set(LAUNCH_MODULES), (
        f"launch.py loads {sorted(loaded)}; tests/launchdeps.py names {sorted(LAUNCH_MODULES)}"
    )


def test_the_sandbox_list_holds_every_squad_module_launch_loads():
    """#1862: the list's writer protocol comes from `squad/`, so a sandbox must host it where the launcher looks."""
    _, loaded = _loads((SCRIPTS / "launch.py").read_text())
    assert loaded, "launch.py loads no squad/ module; the read is broken, not the list"
    assert loaded == set(SQUAD_MODULES), (
        f"launch.py loads {sorted(loaded)} from squad/; tests/launchdeps.py names {sorted(SQUAD_MODULES)}"
    )


def _launcher_function(src, name):
    """One function of launch.py, compiled alone: importing the script would start a server or a run."""
    tree = ast.parse(src)
    keep = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name]
    assert keep, f"launch.py no longer ships {name}"
    return compile(ast.Module(body=keep[:1], type_ignores=[]), "launch.py", "exec")


def test_the_installed_module_sits_where_the_launcher_resolves_it(tmp_path, monkeypatch):
    """`install_squad_modules(root)` must place the file under the same root the launcher reads `$SG_SQUAD` from."""
    here = tmp_path / "scripts"
    here.mkdir()
    install_squad_modules(tmp_path)
    expected = tmp_path / "squad" / "active_runs.py"
    assert expected.is_file(), "the helper did not install the module under <root>/squad/"
    monkeypatch.setenv("SG_SQUAD", str(tmp_path))
    ns = {"here": here, "os": os, "sys": sys, "pathlib": pathlib, "importlib": importlib,
          "squad": str(tmp_path / "installed")}
    exec(_launcher_function((SCRIPTS / "launch.py").read_text(), "_active_runs_module"), ns)
    mod = ns["_active_runs_module"]()
    assert Path(mod.__file__).resolve() == expected.resolve(), (
        f"the launcher resolved {mod.__file__}, not the installed {expected}"
    )


def test_a_squad_load_spelled_through_a_variable_is_read():
    """A load whose path is a name bound to the join is still a `squad/` load; a line scan would miss it."""
    src = (
        "import importlib, pathlib\n"
        'path = pathlib.Path(root) / "squad" / "extra.py"\n'
        'spec = importlib.util.spec_from_file_location("extra", path)\n'
    )
    _, squad = _loads(src)
    assert squad == {"extra.py"}, "a squad/ load spelled through a variable escaped the guard"
