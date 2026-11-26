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


def test_a_leaked_proxy_is_described_by_its_command_line(tmp_path):
    """#2989: the leak guard names each survivor's command line, whose refusals path holds the test's own folder."""
    from tests.proxystop import describe
    proc = subprocess.Popen([sys.executable, str(PROXY), "--allow", "localhost:9", "--scope", "t",
                             "--refusals", str(tmp_path / "refusals.jsonl")],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, start_new_session=True)
    try:
        assert proc.stdout.readline().startswith("port ")
        text = describe([proc.pid])
        assert f"{proc.pid}:" in text and str(tmp_path / "refusals.jsonl") in text, text
    finally:
        end_proxy(proc.pid)
