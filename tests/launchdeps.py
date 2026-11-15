"""The modules launch.py loads, for every test sandbox that runs it (#1832, #1862).

launch.py loads provkey, modelreg, router_envelope and liveholds by path from its own folder. A pytest case that runs it
copies it into a throwaway folder, so the copy list must hold every one of them. #1829 gave launch.py a fourth load and the
lists went stale, so three module's launch tests died with FileNotFoundError on every tier. One constant names the list;
tests/test_launch_modules.py fails when launch.py gains a load this constant lacks.

#1862 gave launch.py a second kind of load: `squad/active_runs.py`, the active-runs list's one writer protocol, read from
a root (`$SG_SQUAD` first) and never beside itself. A sandbox that hosts no such file is refused before its run starts, so
a second constant names it and `install_squad_modules` puts it where the launcher looks.
"""
import shutil
from pathlib import Path

LAUNCH_MODULES = ("liveholds.py", "modelreg.py", "provkey.py", "router_envelope.py")
SQUAD_MODULES = ("active_runs.py",)
SQUAD = Path(__file__).resolve().parents[1] / "squad"


def install_squad_modules(squad_root):
    """Copy every `squad/` module launch.py loads into `<squad_root>/squad/`, where its root search finds it."""
    dest = Path(squad_root) / "squad"
    dest.mkdir(parents=True, exist_ok=True)
    for name in SQUAD_MODULES:
        shutil.copy(SQUAD / name, dest / name)
