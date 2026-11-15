"""A test sandbox that runs launch.py provides every module it loads (#1832, #1862).

launch.py loads a sibling module by path with `spec_from_file_location(..., here / "<name>.py")`. The pytest cases that run
it in a throwaway folder copy them, and a missing one is a FileNotFoundError at import time, not a failure of what the case
checks. It also loads `squad/<name>.py` from a root (`$SG_SQUAD` first), and a sandbox that hosts no such file is refused
before the run starts. This guard reads both loads out of launch.py, so a new one fails here before it fails three suites
on every tier.
"""
import re
from pathlib import Path

from tests.launchdeps import LAUNCH_MODULES, SQUAD_MODULES

SCRIPTS = Path(__file__).resolve().parents[1] / "contrib/squad-goal/scripts"
LOAD = re.compile(r'spec_from_file_location\([^,]+,\s*here\s*/\s*"([^"]+)"\)')


def _loaded_squad_modules(src):
    """Every `.py` a `spec_from_file_location` line takes out of a `squad/` folder, never beside itself."""
    out = set()
    for line in src.splitlines():
        if "spec_from_file_location" in line and '"squad"' in line and "here" not in line:
            out |= set(re.findall(r'"([^"]+\.py)"', line))
    return out


def test_the_sandbox_list_holds_every_module_launch_loads():
    loaded = set(LOAD.findall((SCRIPTS / "launch.py").read_text()))
    assert loaded, "launch.py loads no sibling module; the read is broken, not the list"
    assert loaded == set(LAUNCH_MODULES), (
        f"launch.py loads {sorted(loaded)}; tests/launchdeps.py names {sorted(LAUNCH_MODULES)}"
    )


def test_the_sandbox_list_holds_every_squad_module_launch_loads():
    """#1862: the list's writer protocol comes from `squad/`, so a sandbox must host it where the launcher looks."""
    loaded = _loaded_squad_modules((SCRIPTS / "launch.py").read_text())
    assert loaded, "launch.py loads no squad/ module; the read is broken, not the list"
    assert loaded == set(SQUAD_MODULES), (
        f"launch.py loads {sorted(loaded)} from squad/; tests/launchdeps.py names {sorted(SQUAD_MODULES)}"
    )
