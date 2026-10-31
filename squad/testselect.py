"""Name the test files a change reaches, by imports and by the paths tests name; answer `all` with a reason when neither can say."""
import argparse
import ast
import subprocess
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

INERT = "squad/testselect_inert.txt"
SHARED = {"pyproject.toml", "uv.lock", "squad/testselect.py", INERT}
DYNAMIC = {"import_module", "spec_from_file_location", "__import__"}
LITERAL = {"import_module", "__import__"}
# A data file under tests/ is read by one module; its importers are the suites it reaches.
DATA_READERS = {"tests/shell_suites_seconds.txt": "tests/shell_suites.py"}


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
    """The module names a file reaches, whether it imports something dynamically, the paths its string literals name, and its declared DEPENDS."""
    names, dynamic, literals, declared = set(), False, set(), set()
    name = _module(rel)
    pkg = name if rel.endswith("/__init__.py") else name.rpartition(".")[0]
    tree = ast.parse(text, filename=rel)
    for node in tree.body:
        # A test that loads a module by path declares the paths it depends on, so the selector can answer from them.
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "DEPENDS" for t in node.targets):
            if isinstance(node.value, (ast.Tuple, ast.List)):
                declared |= {e.value for e in node.value.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)}
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            literals.add(node.value.removeprefix("./").rstrip("/"))
        elif isinstance(node, ast.Import):
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
    return names, dynamic, literals, declared


def _scan_file(job):
    root, rel = job
    try:
        return rel, _scan(rel, (root / rel).read_text())
    except (SyntaxError, ValueError):
        return rel, None


def _inert(root):
    lines = (root / INERT).read_text().splitlines()
    return {line.strip() for line in lines if line.strip() and not line.startswith("#")}


def _named_by(named, rel):
    parts = rel.split("/")
    hits = set()
    for i in range(1, len(parts) + 1):
        hits |= named.get("/".join(parts[:i]), set())
    return hits


def _reach(start, start_rel, importers, dynamic):
    reached, queue, seen = set(), [start], set()
    while queue:
        name = queue.pop()
        if name in seen:
            continue
        seen.add(name)
        for rel in importers.get(name, ()):
            # A test that declared the changed path is answered by that declaration; one that declared another path only
            # proves nothing about this edge, so the edge still answers `all` rather than drop it.
            if rel in dynamic and start_rel not in dynamic[rel]:
                _all(f"{rel} imports dynamically and reaches {start_rel}")
            if _is_test(rel):
                reached.add(rel)
            else:
                queue.append(_module(rel))
    return reached


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
    if not changed:
        return []

    inert = _inert(root)
    live = []
    for rel in changed:
        if rel in SHARED or rel.startswith("config/"):
            _all(f"{rel} is shared by every test")
        if rel in DATA_READERS:
            live.append(DATA_READERS[rel])
            continue
        if rel.startswith("tests/") and not _is_test(rel):
            _all(f"{rel} is a test helper, not a test file")
        if rel not in inert:
            live.append(rel)
    if not live:
        return []

    rels = sorted(
        path.relative_to(root).as_posix()
        for folder in ("squad", "tests")
        for path in (root / folder).rglob("*.py")
        if "__pycache__" not in path.parts
    )
    with ProcessPoolExecutor() as pool:
        scanned = list(pool.map(_scan_file, [(root, rel) for rel in rels], chunksize=8))
    importers, dynamic, named = {}, {}, {}
    for rel, scan in scanned:
        if scan is None:
            _all(f"{rel} does not parse")
        names, dyn, literals, declared = scan
        for name in names:
            importers.setdefault(name, set()).add(rel)
        if dyn:
            dynamic[rel] = declared
        if _is_test(rel):
            for literal in literals:
                named.setdefault(literal, set()).add(rel)

    selected = set()
    for rel in live:
        hits = _named_by(named, rel)
        if _is_test(rel):
            hits.add(rel)
        elif (rel.startswith("squad/") or rel in DATA_READERS.values()) and rel.endswith(".py"):
            hits |= _reach(_module(rel), rel, importers, dynamic)
        if not hits:
            _all(f"{rel} is reached by no test: no import, no path literal, not inert")
        selected |= hits
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
