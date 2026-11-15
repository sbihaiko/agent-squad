"""The modules launch.py loads beside itself, for every test sandbox that runs it (#1832).

launch.py loads provkey, modelreg, router_envelope and liveholds by path from its own folder. A pytest case that runs it
copies it into a throwaway folder, so the copy list must hold every one of them. #1829 gave launch.py a fourth load and the
lists went stale, so three module's launch tests died with FileNotFoundError on every tier. One constant names the list;
tests/test_launch_modules.py fails when launch.py gains a load this constant lacks.
"""
LAUNCH_MODULES = ("liveholds.py", "modelreg.py", "provkey.py", "router_envelope.py")
