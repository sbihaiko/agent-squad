"""issue_state has no wave-to-milestone mapping (#1987, SPEC 1965).

A wave is an ordering label only. The owner decided that nothing reads a milestone by wave, so the seam carries no
wave read, no delivery write and no milestone write. This case reads the module's own surface: a name that is gone stays
gone, and a name that comes back fails here.
"""
import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "contrib" / "squad-goal" / "scripts" / "issue_state.py"
SPEC = importlib.util.spec_from_file_location("issue_state_wave", SCRIPT)
issue_state = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(issue_state)

RETIRED_ON_STATE = ("wave", "deliver_wave")
RETIRED_ON_BINDING = ("issue_wave", "milestone", "write_milestone")
RETIRED_ON_MODULE = ("WaveSpansRepositories", "WAVE")


def test_the_seam_carries_no_wave_read_and_no_delivery_write():
    missing = [n for n in RETIRED_ON_STATE if hasattr(issue_state.State, n)]
    assert not missing, f"the wave mapping is back on the seam: {missing}"


def test_the_binding_carries_no_milestone_write():
    missing = [n for n in RETIRED_ON_BINDING if hasattr(issue_state.BoardBinding, n)]
    assert not missing, f"the milestone mapping is back on the binding: {missing}"


def test_the_module_carries_no_wave_constant_and_no_wave_error():
    present = [n for n in RETIRED_ON_MODULE if hasattr(issue_state, n)]
    assert not present, f"the retired wave names are back on the module: {present}"
