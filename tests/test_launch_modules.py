"""A test sandbox that runs launch.py copies every module it loads beside itself (#1832).

launch.py loads a sibling module by path with `spec_from_file_location(..., here / "<name>.py")`. The pytest cases that run
it in a throwaway folder copy them, and a missing one is a FileNotFoundError at import time, not a failure of what the case
checks. This guard reads the loads out of launch.py, so a new one fails here before it fails three suites on every tier.
"""
import re
from pathlib import Path

from tests.launchdeps import LAUNCH_MODULES

SCRIPTS = Path(__file__).resolve().parents[1] / "contrib/squad-goal/scripts"
LOAD = re.compile(r'spec_from_file_location\([^,]+,\s*here\s*/\s*"([^"]+)"\)')


def test_the_sandbox_list_holds_every_module_launch_loads():
    loaded = set(LOAD.findall((SCRIPTS / "launch.py").read_text()))
    assert loaded, "launch.py loads no sibling module; the read is broken, not the list"
    assert loaded == set(LAUNCH_MODULES), (
        f"launch.py loads {sorted(loaded)}; tests/launchdeps.py names {sorted(LAUNCH_MODULES)}"
    )
