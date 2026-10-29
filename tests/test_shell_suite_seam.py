"""One seam runs a shell suite: `tests.shell_suites.run` clears every inherited `SG_*` and `SERIAL_*`, sets its own `HOME`
and bounds the call by the suite's own measure (#736, #864).

A test module that spawns a suite itself hands it the live loop's environment: env.sh exports SG_MOVE_BY=loop (#861) and its
own defaults, so a suite that asserts a bare environment goes red in a gate the loop runs and green in a developer's shell.
The two modules that did this are routed through the seam, and this guard keeps a new one from doing it again.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

TESTS = Path(__file__).resolve().parent
SPAWNERS = {"run", "Popen", "check_output", "check_call", "call"}
SUITE = re.compile(r"test-[A-Za-z0-9_.-]+\.sh")


def _bare_spawns(source: Path):
    """(line, suite) for each `subprocess` call in `source` that starts a suite by name, whatever its `env=`.

    The suite is read from the call's own source segment, so a name built at run time (`str(SCRIPTS / "test-x.sh")`) is
    found beside a literal one."""
    text = source.read_text()
    for node in ast.walk(ast.parse(text)):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in SPAWNERS or not isinstance(node.func.value, ast.Name) or node.func.value.id != "subprocess":
            continue
        for arg in node.args:
            segment = ast.get_source_segment(text, arg) or ""
            if "bash" not in segment:
                continue
            for name in SUITE.findall(segment):
                yield node.lineno, name
            break


def test_no_test_module_spawns_a_shell_suite_outside_the_one_seam() -> None:
    offenders = [f"{source.name}:{line} spawns {name}" for source in sorted(TESTS.glob("test_*.py"))
                 for line, name in _bare_spawns(source)]
    assert offenders == [], ("these spawn a shell suite with the ambient environment: run it through "
                             f"tests.shell_suites.run instead: {offenders}")
