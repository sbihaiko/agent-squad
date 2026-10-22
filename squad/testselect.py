"""Name the test files a change reaches, by imports; answer `all` with a reason when the imports cannot say."""
import argparse
import ast
import subprocess
from pathlib import Path

SHARED = {"pyproject.toml", "uv.lock", "squad/testselect.py"}
DYNAMIC = {"import_module", "spec_from_file_location", "__import__"}
LITERAL = {"import_module", "__import__"}


class _All(Exception):
    pass


def _all(reason):
    raise _All(reason)


def _git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)


def _is_test(rel):
    return rel.startswith("tests/") and rel.endswith(".py") and Path(rel).name.startswith("test_")


def _module(rel):
    parts = rel[:-3].split("/")
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _with_ancestors(name):
    parts = name.split(".")
    return {".".join(parts[:i]) for i in range(1, len(parts) + 1)}


def _scan(rel, text):
    """The module names a file reaches, and whether it imports something dynamically."""
    names, dynamic = set(), False
    name = _module(rel)
    pkg = name if rel.endswith("/__init__.py") else name.rpartition(".")[0]
    for node in ast.walk(ast.parse(text, filename=rel)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names |= _with_ancestors(alias.name)
        elif isinstance(node, ast.ImportFrom):
            target = node.module or ""
            if node.level:
                parts = pkg.split(".")
                base = ".".join(parts[: len(parts) - node.level + 1])
                target = ".".join(p for p in (base, target) if p)
            if target:
                names |= _with_ancestors(target)
                for alias in node.names:
                    names |= _with_ancestors(f"{target}.{alias.name}")
        elif isinstance(node, ast.Call):
            fn = node.func
            called = fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else ""
            if called in DYNAMIC:
                arg = node.args[0] if node.args else None
                if called in LITERAL and isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    names |= _with_ancestors(arg.value)
                else:
                    dynamic = True
    return names, dynamic


def _answer(root, base):
    if base.startswith("-") or _git(root, "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}").returncode:
        _all(f"base {base} does not resolve")
    merge_base = _git(root, "merge-base", base, "HEAD")
    if merge_base.returncode:
        _all(f"no merge-base between {base} and HEAD")
    diff = _git(root, "diff", "-z", "--name-only", "--no-renames", merge_base.stdout.strip(), "HEAD")
    if diff.returncode:
        _all(f"the diff against {base} failed")
    changed = [p for p in diff.stdout.split("\0") if p]

    modules, tests = {}, set()
    for rel in changed:
        if rel in SHARED or rel.startswith("config/"):
            _all(f"{rel} is shared by every test")
        if rel.startswith("tests/"):
            if not _is_test(rel):
                _all(f"{rel} is a test helper, not a test file")
            tests.add(rel)
        elif rel.startswith("squad/") and rel.endswith(".py"):
            modules[_module(rel)] = rel
        else:
            _all(f"{rel} is not a squad module or a test file")
    if not changed:
        return []

    importers, dynamic = {}, set()
    for folder in ("squad", "tests"):
        for path in sorted((root / folder).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            rel = path.relative_to(root).as_posix()
            try:
                names, dyn = _scan(rel, path.read_text())
            except (SyntaxError, ValueError):
                _all(f"{rel} does not parse")
            for name in names:
                importers.setdefault(name, set()).add(rel)
            if dyn:
                dynamic.add(rel)

    selected = set(tests)
    for start, start_rel in sorted(modules.items()):
        reached, queue, seen = set(), [start], set()
        while queue:
            name = queue.pop()
            if name in seen:
                continue
            seen.add(name)
            for rel in importers.get(name, ()):
                if rel in dynamic:
                    _all(f"{rel} imports dynamically and reaches {start_rel}")
                if _is_test(rel):
                    reached.add(rel)
                else:
                    queue.append(_module(rel))
        if not reached:
            _all(f"no test imports {start_rel}")
        selected |= reached
    return sorted(selected)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python3 -m squad.testselect")
    parser.add_argument("--base", required=True)
    parser.add_argument("--worktree", default=".")
    args = parser.parse_args(argv)
    try:
        for path in _answer(Path(args.worktree).resolve(), args.base):
            print(path)
    except _All as reason:
        print(f"all: {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
