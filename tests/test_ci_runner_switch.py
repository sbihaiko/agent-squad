import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
E2E = ROOT / "tests" / "test_gui_test_e2e.py"
NODE_BOUND = ROOT / "tests" / "test_hub_mobile_board.py"
INSTALL = ROOT / "docs" / "install.md"
MESENAI_NAMES = ("MESENAI_REPO", "MESENAI_ROOT")
HOST_BOUND = re.compile(r"@pytest\.mark\.host_bound(?:\([^\n]*\))?\s*\ndef (\w+)\(")


def _host_bound_names(path: Path) -> set[str]:
    return set(HOST_BOUND.findall(path.read_text()))


def _collect(path: Path, extra: dict, drop: tuple = ()) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in drop}
    env.update(extra)
    return subprocess.run([sys.executable, "-m", "pytest", str(path), "--collect-only", "-p", "no:cacheprovider"],
                          cwd=ROOT, env=env, capture_output=True, text=True)


@pytest.mark.heavy
def test_the_ci_runner_switch_deselects_each_fixture_bound_test_by_id_and_never_skips_it():
    """Guard for #1323: SQUAD_CI_RUNNER=1 hides the MesenAI checkout; every host-bound test is deselected, printed by its
    id with its reason, and none is skipped."""
    r = _collect(E2E, {"SQUAD_CI_RUNNER": "1"}, drop=MESENAI_NAMES)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    host = _host_bound_names(E2E)
    assert host, "the e2e file marks no host-bound test"
    for name in sorted(host):
        assert f"deselected: tests/test_gui_test_e2e.py::{name} -- " in out, name
    assert f"{len(host)} test(s) deselected" in out, out
    assert "skipped" not in out, "a host-bound test was skipped, not deselected"


@pytest.mark.heavy
def test_the_ci_runner_switch_hides_the_node_binary_and_deselects_the_node_bound_tests():
    """Guard for #1323: the switch hides the node binary too, so a node-bound test is deselected and printed by id."""
    r = _collect(NODE_BOUND, {"SQUAD_CI_RUNNER": "1"})
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "deselected: tests/test_hub_mobile_board.py::" in out, out
    assert "runs the page's JavaScript under node" in out, out


@pytest.mark.heavy
def test_with_the_fixtures_present_no_test_is_deselected(tmp_path):
    """Guard for #1323: with the MesenAI checkout present and the switch off, nothing is deselected."""
    r = _collect(E2E, {"MESENAI_REPO": str(tmp_path), "MESENAI_ROOT": str(tmp_path)}, drop=("SQUAD_CI_RUNNER",))
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "deselected" not in out, out
    host = next(iter(sorted(_host_bound_names(E2E))))
    assert f"tests/test_gui_test_e2e.py::{host}" in out, out


def test_every_host_bound_marker_names_the_fixture_in_its_reason():
    """Guard for #1323: a host-bound test carries a reason that names the MesenAI checkout it needs, never a bare marker."""
    text = E2E.read_text()
    assert not re.search(r"@pytest\.mark\.host_bound\s*\n", text), "a host-bound marker carries no reason"
    reasons = re.findall(r'@pytest\.mark\.host_bound\(reason="([^"]+)"\)', text)
    assert len(reasons) == len(_host_bound_names(E2E)), reasons
    assert all("MESENAI_REPO" in reason for reason in reasons), reasons


def test_the_ci_runner_switch_is_documented_beside_the_test_commands():
    """Guard for #1323: the install guide names the switch next to the suite commands, so an owner finds it."""
    text = INSTALL.read_text()
    assert "SQUAD_CI_RUNNER=1" in text
    assert text.index("SQUAD_CI_RUNNER=1") > text.index("pytest -n auto")



def _ids(paths: tuple, extra: dict) -> tuple[set[str], str]:
    """The node ids pytest collects from the files with the switches given and both nightly switches otherwise off."""
    env = {k: v for k, v in os.environ.items() if k not in ("SQUAD_CI_RUNNER", "SQUAD_NIGHTLY_LOCAL_HALF")}
    env.update(extra)
    r = subprocess.run([sys.executable, "-m", "pytest", *map(str, paths), "--collect-only"],
                       cwd=ROOT, env=env, capture_output=True, text=True)
    out = r.stdout + r.stderr
    assert r.returncode in (0, 5), out
    return {line.strip() for line in r.stdout.splitlines() if "::" in line and not line.startswith("deselected:")}, out


@pytest.mark.heavy
def test_the_local_half_switch_keeps_only_what_a_linux_runner_deselects():
    """Guard for the nightly's local half (#1975, ADR-0153, ADR-0139): `SQUAD_NIGHTLY_LOCAL_HALF=1` keeps only the tests
    that a Linux runner deselects by marker, read from the conftest's one deselection rule, never from a second list.

    The node-bound gesture and board tests stay where node runs, and the unmarked tests of this file are left to the GitHub half.
    """
    paths = (NODE_BOUND, Path(__file__))
    every, _ = _ids(paths, {})
    linux_runs, _ = _ids(paths, {"SQUAD_CI_RUNNER": "1"})
    local, out = _ids(paths, {"SQUAD_NIGHTLY_LOCAL_HALF": "1"})
    assert linux_runs and linux_runs < every, "a Linux runner deselects none of these tests, so the case proves nothing"
    assert not (local & linux_runs), f"the local half kept a test the Linux runner runs: {sorted(local & linux_runs)[:3]}"
    if shutil.which("node"):
        assert local == every - linux_runs, f"the local half is not what the Linux runner deselects: {out}"
