#!/usr/bin/env python3
# usage: quality-reading.py <base>   run in a tip's worktree, after the base battery passes (#885)
#
# Prints radon's complexity of the squad modules the tip changes and mutmut's surviving mutants of the functions it changes.
# A report beside the pytest counts, never a gate: the exit is 0 on every path (ADR-0012, ADR-0018). A missing, failed or
# overdue tool is a named skip. The tools are dev-only and run as subprocesses, never imported. Mutation runs in a copy of
# HEAD, so the worktree gets no file.
import ast
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

RADON_DEADLINE = 20
MUTMUT_DEADLINE = 60
CLASS_SEPARATOR = "ǁ"  # mutmut's separator in a method's mutant name
HUNK = re.compile(r"^@@ -\S+ \+(\d+)(?:,(\d+))? @@")
FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)


def deadline(name, default):
    return float(os.environ.get(f"SG_QUALITY_{name}_DEADLINE", default))


def plural(count, noun):
    return f"{count} {noun}{'' if count == 1 else 's'}"


def git(*args, check=True):
    return subprocess.run(["git", *args], capture_output=True, text=True, check=check).stdout


def call(argv, cwd, limit):
    proc = subprocess.Popen(argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            text=True, start_new_session=True)
    try:
        out, _ = proc.communicate(timeout=max(limit, 0.01))
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()
        return None, ""
    return proc.returncode, out


def radon_line(files):
    tool = shutil.which("radon")
    if tool is None:
        return "quality: radon not installed, skipped"
    limit = deadline("RADON", RADON_DEADLINE)
    rc, out = call([tool, "cc", "-s", "-j", *files], os.getcwd(), limit)
    if rc is None:
        return f"quality: radon deadline of {limit:g}s passed, skipped"
    if rc != 0:
        return f"quality: radon failed (exit {rc}), skipped"
    blocks = [(path, block["name"], block["complexity"])
              for path, found in json.loads(out).items() for block in found]
    if not blocks:
        return "quality: radon cc: no block in the changed modules"
    mean = sum(complexity for _, _, complexity in blocks) / len(blocks)
    path, name, worst = max(blocks, key=lambda block: block[2])
    return (f"quality: radon cc: {len(blocks)} blocks in {plural(len(files), 'changed module')}, "
            f"mean CC {mean:.1f}, max CC {worst} ({path}::{name})")


def changed_lines(merge_base, path):
    lines = set()
    for line in git("diff", "-U0", merge_base, "HEAD", "--", path).splitlines():
        hunk = HUNK.match(line)
        if hunk:
            start, count = int(hunk.group(1)), int(hunk.group(2) or 1)
            lines.update(range(start, start + max(count, 1)))
    return lines


def functions(tree):
    for node in tree.body:
        if isinstance(node, FUNCTIONS):
            yield f"x_{node.name}", node
        elif isinstance(node, ast.ClassDef):
            for child in node.body:
                if isinstance(child, FUNCTIONS):
                    yield f"x{CLASS_SEPARATOR}{node.name}{CLASS_SEPARATOR}{child.name}", child


def mutant_names(merge_base, files):
    names = []
    for path in files:
        module = path[: -len(".py")].replace("/", ".").removesuffix(".__init__")
        lines = changed_lines(merge_base, path)
        for key, func in functions(ast.parse(git("show", f"HEAD:{path}"))):
            first = min([func.lineno, *(decorator.lineno for decorator in func.decorator_list)])
            if lines.intersection(range(first, func.end_lineno + 1)):
                names.append(f"{module}.{key}__mutmut_*")
    return names


def mutmut_line(tool, names):
    limit = deadline("MUTMUT", MUTMUT_DEADLINE)
    expired = f"quality: mutmut deadline of {limit:g}s passed, skipped"
    until = time.monotonic() + limit

    def remaining():
        return max(until - time.monotonic(), 0.01)

    tmp = tempfile.mkdtemp(prefix="quality-reading-")
    try:
        archive = subprocess.run(["git", "archive", "HEAD"], capture_output=True, check=True, timeout=remaining()).stdout
        subprocess.run(["tar", "-x", "-f", "-", "-C", tmp], input=archive, check=True, timeout=remaining())
        if call([tool, "run", *names], tmp, remaining())[0] is None:
            return expired
        if call([tool, "export-cicd-stats"], tmp, remaining())[0] is None:
            return expired
        stats_path = os.path.join(tmp, "mutants", "mutmut-cicd-stats.json")
        if not os.path.exists(stats_path):
            return "quality: mutmut wrote no stats, skipped"
        with open(stats_path) as handle:
            stats = json.load(handle)
    except subprocess.TimeoutExpired:
        return expired
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return (f"quality: mutmut: {stats['survived']} survived of {stats['total']} mutants "
            f"in {plural(len(names), 'changed function')}")


def mutmut_report(tool, merge_base, files):
    names = mutant_names(merge_base, files)
    if not names:
        return "quality: no changed function to mutate, skipped"
    return mutmut_line(tool, names)


def stage(name, run):
    try:
        return run()
    except (OSError, ValueError, KeyError, TypeError, SyntaxError, subprocess.CalledProcessError) as err:
        return f"quality: {name} output unreadable ({type(err).__name__}), skipped"


def main():
    base = sys.argv[1]
    merge_base = git("merge-base", "HEAD", base, check=False).strip()
    if not merge_base:
        print(f"quality: base {base} does not resolve, skipped")
        return
    files = git("diff", "--name-only", "--diff-filter=d", merge_base, "HEAD", "--", "squad/*.py").split()
    if not files:
        print("quality: no changed squad module, skipped")
        return
    print(stage("radon", lambda: radon_line(files)))
    tool = shutil.which("mutmut")
    if tool is None:
        print("quality: mutmut not installed, skipped")
        return
    print(stage("mutmut", lambda: mutmut_report(tool, merge_base, files)))


if __name__ == "__main__":
    main()
