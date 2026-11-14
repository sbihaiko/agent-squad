"""Guard: issue #1686. A model proxy this process starts is listed while it runs and is gone once it is ended."""
import os
import subprocess
import sys
from pathlib import Path

from tests.proxystop import end_proxy, live_proxy_pids

PROXY = Path(__file__).resolve().parent.parent / "contrib" / "voice" / "model_proxy.py"


def test_a_started_proxy_is_listed_until_it_is_ended(tmp_path):
    proc = subprocess.Popen([sys.executable, str(PROXY), "--allow", "localhost:9", "--scope", "t",
                             "--refusals", str(tmp_path / "refusals.jsonl")],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, start_new_session=True)
    try:
        assert proc.stdout.readline().startswith("port ")
        assert proc.pid in live_proxy_pids(os.getpid())
        end_proxy(proc.pid)
        assert proc.pid not in live_proxy_pids(os.getpid())
    finally:
        end_proxy(proc.pid)
