"""hub.py reads its state directory tolerantly: `$SG_STATE` when the runner exports it, else the state directory the seam
(address.sh) names under the runner's home (#135). The hub is a gate: it must be as green where CI runs it as where the
loop runs it (ADR-0018), so a runner that exports no SG_STATE gets a state directory of its own, never a KeyError at
import and never the checkout the hub was started in.
"""
import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"


def load_hub(name):
    """The hub as a runner loads it: module level code runs, so this is where a hard read of SG_STATE fails."""
    spec = importlib.util.spec_from_file_location(name, HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def runner(monkeypatch, tmp_path):
    """A caller's environment as a runner has it: the profile context, a private home, and no state variable. SG_STATE_BASE
    is cleared too, so the directory the seam names is this runner's own and never the machine's."""
    home, checkout, hubdir = tmp_path / "home", tmp_path / "checkout", tmp_path / "hub"
    for d in (home, checkout, hubdir):
        d.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("SG_REPO", "x/y")
    monkeypatch.setenv("SG_OWNER", "x")
    monkeypatch.setenv("SG_PROJECT", "4")
    monkeypatch.setenv("SG_CHECKOUT", str(checkout))
    monkeypatch.setenv("SG_HUB_DIR", str(hubdir))
    for k in ("SG_STATE", "SG_STATE_BASE", "SG_SHARED", "SG_PROFILE"):
        monkeypatch.delenv(k, raising=False)
    return home, checkout


def test_a_runner_without_sg_state_gets_a_state_directory_under_its_home(runner):
    """The defect: the import raised KeyError: 'SG_STATE' wherever the variable was not exported, so every hub suite was
    green in the loop and red in CI."""
    home, checkout = runner
    hub = load_hub("hub_state_default")
    assert home in hub.STATE.parents, f"the state directory left the runner's home: {hub.STATE}"
    assert checkout not in hub.STATE.parents, f"the state directory fell into the checkout: {hub.STATE}"
    assert hub.STATE != pathlib.Path.cwd(), "a runner without SG_STATE must not read its own cwd as the state"
    assert home in hub.SHARED.parents, f"the shared directory left the runner's home: {hub.SHARED}"


def test_sg_state_still_wins_when_the_runner_exports_it(runner, monkeypatch, tmp_path):
    """The positive control: an exported SG_STATE is used as it stands, even where the runner's home names another one."""
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("SG_STATE", str(state))
    hub = load_hub("hub_state_exported")
    assert hub.STATE == state
