"""A test never assigns to an attribute of `pathlib.Path` by hand (#2483).

A bare assignment outlives its test: `Path.home` stayed patched after `test_sessions.py`, so a later test in the same
process read a temporary home and `plugin_squad()` found no plugin install. `monkeypatch.setattr` restores the value.
"""
import ast
import pathlib
import re

TESTS = pathlib.Path(__file__).resolve().parent
CANDIDATE = re.compile(r"Path\.\w+\s*\+?=(?!=)")   # a cheap text filter: only a file that matches is parsed


def _path_targets(tree):
    for node in ast.walk(tree):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AugAssign) else []
        for t in targets:
            if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Attribute) and t.value.attr == "Path":
                yield node.lineno, t.attr
            if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id == "Path":
                yield node.lineno, t.attr


def test_no_test_assigns_to_a_path_class_attribute():
    texts = {p: p.read_text() for p in sorted(TESTS.glob("test_*.py"))}
    found = [f"{p.name}:{line} Path.{attr}" for p, text in texts.items() if CANDIDATE.search(text)
             for line, attr in _path_targets(ast.parse(text, str(p)))]
    assert not found, "use monkeypatch.setattr, a bare assignment leaks into later tests: " + ", ".join(found)
