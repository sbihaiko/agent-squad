"""Name the test files a change reaches, by imports and by the paths tests name; answer `all` with a reason when neither can
say, and `none` with a reason when the change reaches no test."""
import argparse
import ast
import subprocess
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

INERT = "squad/testselect_inert.txt"
SHARED = {"pyproject.toml", "uv.lock", "squad/testselect.py", INERT}
DYNAMIC = {"import_module", "spec_from_file_location", "__import__"}
LITERAL = {"import_module", "__import__"}
LISTERS = {"glob", "rglob", "iterdir", "listdir", "walk", "scandir"}
# A data file under tests/ is read by one module; its importers are the suites it reaches.
DATA_READERS = {"tests/shell_suites_seconds.txt": "tests/shell_suites.py"}
# The shard guard names every top-level test file, so a file added or deleted reaches it and no other test.
SHARD_GUARD = "tests/test_tooling.py"
# A helper that is not a test file is read by the tests that name it; the one whose reader is known is the shard map, which
# every card edits to give a new test file its shard line (#1870).
HELPER_READERS = {"tests/ci_shards.py": SHARD_GUARD}
# The measured-line guard reads every `test-*.sh` beside the scripts and fails a suite with no line in
# tests/shell_suites_seconds.txt, so a suite added there reaches it and no other test (#1799).
SHELL_SUITES = "contrib/squad-goal/scripts"
MEASURED_LINE_GUARD = "tests/test_shell_suite_runner.py"
# The gate stages that may run the delta: the tests a change reaches. The landing, the promotion proof and the nightly
# always run the full battery, so a cheaper check never weakens the gate a delivery stands on (ADR-0018). A stage this
# list does not name is full too: a typo in a stage never narrows a gate.
DELTA_STAGES = frozenset({"fast", "wave-entry", "wave-tip", "prepush"})


class _All(Exception):
    pass


def _all(reason):
    raise _All(reason)


class _None(Exception):
    pass


def _none(reason):
    raise _None(reason)


def _git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)


def _is_test(rel):
    return rel.startswith("tests/") and rel.endswith(".py") and Path(rel).name.startswith("test_")


def _is_shell_suite(rel):
    path = Path(rel)
    return path.parent.as_posix() == SHELL_SUITES and path.name.startswith("test-") and path.suffix == ".sh"


def _module(rel):
    parts = rel[:-3].split("/")
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _with_ancestors(name):
    parts = name.split(".")
    return {".".join(parts[:i]) for i in range(1, len(parts) + 1)}


def _norm(text):
    # A joined tail such as SCRIPTS + "/watchd.sh" names the file by its basename, so a leading slash is dropped too.
    return text.removeprefix("./").strip("/")


def _operands(node):
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return _operands(node.left) + [node.right]
    return [node]


def _joins(node):
    """The full text of each run of constants in a path join."""
    found, run = set(), []
    for part in _operands(node):
        if isinstance(part, ast.Constant) and isinstance(part.value, str):
            run.append(_norm(part.value))
        elif run:
            found.add("/".join(run))
            run = []
    if run:
        found.add("/".join(run))
    return found


def _prefixes(text):
    parts = text.split("/")
    return {"/".join(parts[:i]) for i in range(1, len(parts) + 1)}


def _strings(node):
    """The strings a subtree names: each constant, and each path join's constant run with every prefix of it."""
    found = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            found.add(_norm(sub.value))
        elif isinstance(sub, ast.BinOp) and isinstance(sub.op, ast.Div):
            for run in _joins(sub):
                found |= _prefixes(run)
    return found


def _listed(call):
    """The directories a listing call names: its receiver and arguments, each as a whole path."""
    exprs = list(call.args) + ([call.func.value] if isinstance(call.func, ast.Attribute) else [])
    found = set()
    for expr in exprs:
        if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
            found.add(_norm(expr.value))
        elif isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Div):
            found |= _joins(expr)
    return found


def _callee(call):
    fn = call.func
    return fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else ""


def _scan(rel, text):
    """The module names a file reaches, whether it imports something dynamically, the paths its string literals name, the
    directories it lists, and its declared DEPENDS."""
    names, dynamic, declared = set(), False, set()
    name = _module(rel)
    pkg = name if rel.endswith("/__init__.py") else name.rpartition(".")[0]
    tree = ast.parse(text, filename=rel)
    for node in tree.body:
        # A test that loads a module by path declares the paths it depends on, so the selector can answer from them.
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "DEPENDS" for t in node.targets):
            if isinstance(node.value, (ast.Tuple, ast.List)):
                declared |= {e.value for e in node.value.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)}
    literals = _strings(tree)
    lists = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _callee(node) in LISTERS:
            lists |= _listed(node)
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
            called = _callee(node)
            if called in DYNAMIC:
                arg = node.args[0] if node.args else None
                if called in LITERAL and isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    names |= _with_ancestors(arg.value)
                else:
                    dynamic = True
    return names, dynamic, literals, lists, declared


def _scan_file(job):
    root, rel = job
    try:
        return rel, _scan(rel, (root / rel).read_text())
    except (SyntaxError, ValueError):
        return rel, None


def _inert(root):
    lines = (root / INERT).read_text().splitlines()
    return {line.strip() for line in lines if line.strip() and not line.startswith("#")}


def _existing(root, literal):
    """The literal as a file of the tree, or None. A module carries prose and long strings, and a stat that cannot answer
    proves nothing: the literal is no path, and the edge it would settle stays unknown (#1878)."""
    try:
        return (root / literal) if len(literal) < 256 and (root / literal).is_file() else None
    except (OSError, ValueError):
        return None


def _named_by(named, listed, rel):
    # A test names a file by its path, a trailing path or its basename. A directory above the file selects a test only
    # with that name, or when the test lists the directory: a test that names a directory has many scripts under it (#1784).
    parts = rel.split("/")
    file_named = set()
    for i in range(len(parts)):
        file_named |= named.get("/".join(parts[i:]), set())
    hits = set(file_named)
    for i in range(1, len(parts)):
        directory = "/".join(parts[:i])
        hits |= named.get(directory, set()) & (file_named | listed.get(directory, set()))
    return hits


def _reach(start, start_rel, importers, dynamic, resolved=()):
    reached, queue, seen = set(), [start], set()
    while queue:
        name = queue.pop()
        if name in seen:
            continue
        seen.add(name)
        for rel in importers.get(name, ()):
            # A test reached by this edge is selected, and nothing else rests on it: no importer's selection can be hidden
            # behind its own dynamic load, so it never answers `all` (ADR-0169). Its module is walked on, so a test that
            # imports from it (a fixture module) is reached too (#2035).
            if _is_test(rel):
                reached.add(rel)
                queue.append(_module(rel))
                continue
            # A module that declared the changed path is answered by that declaration; one that declared another path only
            # proves nothing about this edge, so the edge still answers `all` rather than drop the importers it hides.
            # A load whose literal target the selector scanned is no unknown edge: that target's own imports are in the
            # graph, so this static edge stands (#1878).
            if rel in dynamic and start_rel not in dynamic[rel] and rel not in resolved:
                _all(f"{rel} imports dynamically and reaches {start_rel}")
            queue.append(_module(rel))
    return reached


def _answer(root, base):
    if base.startswith("-") or _git(root, "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}").returncode:
        _all(f"base {base} does not resolve")
    merge_base = _git(root, "merge-base", base, "HEAD")
    if merge_base.returncode:
        _all(f"no merge-base between {base} and HEAD")
    # The delta is the committed diff; a change the tree carries outside it cannot be placed, so it never selects nothing
    # for that change: the answer is the full battery, naming the files (#1249).
    dirt = _git(root, "status", "--porcelain", "-z", "--no-renames")
    if dirt.returncode:
        _all("the tree's uncommitted state cannot be read")
    loose = sorted({entry[3:] for entry in dirt.stdout.split("\0") if len(entry) > 3})
    if loose:
        shown = " ".join(loose[:5]) + (f" and {len(loose) - 5} more" if len(loose) > 5 else "")
        _all(f"the tree carries uncommitted changes the delta cannot place: {shown}")
    diff = _git(root, "diff", "-z", "--name-only", "--no-renames", merge_base.stdout.strip(), "HEAD")
    if diff.returncode:
        _all(f"the diff against {base} failed")
    changed = [p for p in diff.stdout.split("\0") if p]
    if not changed:
        _none(f"the tip changes no file against {base}")
    # A suite the delta adds has no line the repo measured yet, so it selects the guard that reads the list (#1799).
    added = _git(root, "diff", "-z", "--name-only", "--no-renames", "--diff-filter=A", merge_base.stdout.strip(), "HEAD")
    if added.returncode:
        _all(f"the diff against {base} failed")
    added_paths = {p for p in added.stdout.split("\0") if p}

    inert = _inert(root)
    live = []
    for rel in changed:
        if rel in SHARED or rel.startswith("config/"):
            _all(f"{rel} is shared by every test")
        if rel in DATA_READERS:
            live.append(DATA_READERS[rel])
            continue
        if rel.startswith("tests/") and not _is_test(rel):
            if rel in HELPER_READERS:
                live.append(HELPER_READERS[rel])
                continue
            # (#1883) A helper is reached by the tests that import it, like a module of the package; one no test imports
            # answers the full battery below, because that reach is not in the graph.
            live.append(rel)
            continue
        if rel not in inert:
            live.append(rel)
    if not live:
        _none(f"every changed file is inert: {' '.join(changed)}")

    rels = sorted(
        path.relative_to(root).as_posix()
        for folder in ("squad", "tests")
        for path in (root / folder).rglob("*.py")
        if "__pycache__" not in path.parts
    )
    with ProcessPoolExecutor() as pool:
        scanned = list(pool.map(_scan_file, [(root, rel) for rel in rels], chunksize=8))
    # (#1878) A dynamic load names its target by a path literal, and that target is a module like any other: the selector
    # scans it, so what it imports is a real edge of the graph. A load whose target no literal names stays unknown, and a
    # module carrying one still answers the full battery.
    known = set(rels)
    targets = {
        literal
        for rel, scan in scanned
        if scan is not None and scan[1]
        for literal in scan[2]
        if _existing(root, literal) is not None
    }
    extra = sorted(targets - known)
    if extra:
        with ProcessPoolExecutor() as pool:
            scanned += list(pool.map(_scan_file, [(root, rel) for rel in extra], chunksize=8))
        known |= {rel for rel, scan in scanned if scan is not None and rel in targets}
    importers, dynamic, named, listed = {}, {}, {}, {}
    resolved = set()
    for rel, scan in scanned:
        if scan is None:
            if rel in targets:
                continue   # a target that does not parse proves nothing: its module stays an unknown edge
            _all(f"{rel} does not parse")
        names, dyn, literals, lists, declared = scan
        for name in names:
            importers.setdefault(name, set()).add(rel)
        if dyn:
            dynamic[rel] = declared
            files = {literal for literal in literals if _existing(root, literal) is not None}
            if not declared and files and files <= known:
                resolved.add(rel)
        if _is_test(rel):
            for literal in literals:
                named.setdefault(literal, set()).add(rel)
            for directory in lists:
                listed.setdefault(directory, set()).add(rel)

    selected = set()
    for rel in live:
        hits = _named_by(named, listed, rel)
        if _is_test(rel):
            hits.add(rel)
        elif rel in added_paths and _is_shell_suite(rel):
            hits.add(MEASURED_LINE_GUARD)
        elif (rel.startswith(("squad/", "tests/")) or rel in DATA_READERS.values()) and rel.endswith(".py"):
            hits |= _reach(_module(rel), rel, importers, dynamic, resolved)
        if not hits:
            _all(f"{rel} is reached by no test: no import, no path literal, not inert")
        selected |= hits
    added_or_deleted = _git(root, "diff", "-z", "--name-only", "--no-renames", "--diff-filter=AD",
                            merge_base.stdout.strip(), "HEAD")
    if added_or_deleted.returncode:
        _all(f"the diff against {base} failed")
    if any(_is_test(rel) and Path(rel).parent.as_posix() == "tests" for rel in added_or_deleted.stdout.split("\0")):
        selected.add(SHARD_GUARD)
    for rel in list(selected):
        if _is_test(rel):
            selected |= _reach(_module(rel), rel, importers, dynamic, resolved)
    present = sorted(rel for rel in selected if (root / rel).exists())   # pytest cannot run a path the tip deleted
    if not present:
        _all(f"every test the change reaches is deleted at the tip: {' '.join(sorted(selected))}")
    return present


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python3 -m squad.testselect")
    parser.add_argument("--base", required=True)
    parser.add_argument("--worktree", default=".")
    parser.add_argument("--stage", default="fast",
                        help="the gate stage; a delta stage selects tests, any other stage runs the full battery")
    args = parser.parse_args(argv)
    # A full stage never asks the diff: the landing, the promotion proof and the nightly run the whole battery whatever
    # the delta would cover, and the reason says which stage asked for it.
    if args.stage not in DELTA_STAGES:
        print(f"all: {args.stage} runs the full battery")
        return 0
    try:
        for path in _answer(Path(args.worktree).resolve(), args.base):
            print(path)
    except _All as reason:
        print(f"all: {reason}")
    except _None as reason:
        # The delta selects no test. The one line says why, so the gate records the attempt as unproven with that reason,
        # never as a pass (#1249).
        print(f"none: {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
