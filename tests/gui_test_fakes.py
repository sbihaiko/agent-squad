"""Fake adapters for the supervision cases: they crash or hang on purpose (worker-side, importable by spec)."""
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from squad.gui_test_adapter import FakeAdapter


class CrashAdapter(FakeAdapter):
    """Dies by SIGSEGV on its first inject (once per `marker`), leaving a native crash report behind."""

    def __init__(self, out_dir, marker, report_dir=None):
        super().__init__(out_dir)
        self.marker, self.report_dir = Path(marker), report_dir

    def inject(self, name, args):
        if not self.marker.exists():
            self.marker.write_text("crashed")
            print("fatal: native fault in the render thread", file=sys.stderr, flush=True)
            if self.report_dir:
                (Path(self.report_dir) / "Fake-crash.ips").write_text('{"symbol": "Render"}')
            os.kill(os.getpid(), signal.SIGSEGV)
        super().inject(name, args)


class HangAdapter(FakeAdapter):
    """Wedges in wait() with a grandchild process alive; the grandchild pid lands in `pid_file`."""

    def __init__(self, out_dir, pid_file):
        super().__init__(out_dir)
        self.pid_file = pid_file

    def wait(self, condition, timeout):
        child = subprocess.Popen(["sleep", "300"])
        Path(self.pid_file).write_text(str(child.pid))
        time.sleep(300)


class SettingsAdapter(FakeAdapter):
    """Mutates the settings file named in the fixtures, like an app rewriting its own profile."""

    def launch(self, fixtures):
        for p in fixtures.get("restore", []):
            Path(p).write_bytes(b"mutated by the app")
        return super().launch(fixtures)
