"""The quality readings of #881 ship as optional dev tools, never as a runtime dependency of the plugin.

The fast gate prints radon's complexity and mutmut's surviving mutants beside the pytest tally, as a report.
No threshold and no exit code reads them (ADR-0012, ADR-0018), so installing the tools must not touch what
`pip install agent-squad` puts on a machine.
"""
import ast
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text())
QUALITY_TOOLS = {"radon", "coverage", "pylint", "mutmut"}


def test_the_runtime_dependency_list_is_unchanged():
    """A quality tool is never a runtime dep: the plugin imports none of them, so the list stays the three it was."""
    assert PYPROJECT["project"]["dependencies"] == ["anyio>=4", "jsonschema>=4", "pyyaml>=6"]


def test_the_quality_tools_are_dev_extras():
    """Each tool the reading uses is named in the `dev` extra, so it is installed only for development."""
    dev = {name.split(">")[0].split("=")[0].strip() for name in PYPROJECT["project"]["optional-dependencies"]["dev"]}
    assert QUALITY_TOOLS <= dev, f"missing from the dev extra: {sorted(QUALITY_TOOLS - dev)}"


def test_no_squad_module_imports_a_quality_tool():
    """The reading reads the package from outside; a module that imported a tool would make it a runtime dep."""
    offenders = []
    for path in sorted((ROOT / "squad").rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = [node.module.split(".")[0]]
            offenders += [f"{path.relative_to(ROOT)} imports {name}" for name in names if name in QUALITY_TOOLS]
    assert offenders == []


def test_mutmut_is_configured_to_read_squad():
    """`mutmut run` crashes before its first mutant without `source_paths`; the config names the package it reads."""
    assert PYPROJECT["tool"]["mutmut"]["source_paths"] == ["squad"]


def test_mutmut_copies_every_tree_the_suite_reads():
    """mutmut copies only source_paths, tests and also_copy; a tree the suite reads and mutmut does not copy fails every mutant."""
    read_at_test_time = {"pyproject.toml", "CLAUDE.md", "prompts", "contrib", "scripts", "dashboard", "commands", "docs",
                         "config", "bench", "vendor", ".github"}
    present = {name for name in read_at_test_time if (ROOT / name).exists()}
    also_copy = set(PYPROJECT["tool"]["mutmut"]["also_copy"])
    assert present <= also_copy, f"not copied into the mutation tree: {sorted(present - also_copy)}"
