"""The tailnet route to the hub: the serve command to type, and a check that the hub is not on Funnel."""
import json
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

HUB_PORT = 7700
STATUS_ARGV = ["tailscale", "serve", "status", "--json"]


class StatusError(Exception):
    pass


def serve_command(port=HUB_PORT):
    return f"tailscale serve --bg http://127.0.0.1:{port}"


def _proxied_port(proxy):
    return urlsplit(proxy if "://" in proxy else f"http://{proxy}").port


def diagnose(status, port=HUB_PORT):
    """The causes why the hub port is not served to the tailnet only; an empty list passes."""
    web = status.get("Web") or {}
    funnel = status.get("AllowFunnel") or {}
    served = [site for site, entry in web.items()
              if any(_proxied_port(h.get("Proxy", "")) == port for h in (entry.get("Handlers") or {}).values())]
    if not served:
        return [f"hub port {port} is not served to the tailnet (no serve handler proxies to it)"]
    return [f"hub port {port} is on Funnel at {site} (AllowFunnel is true)" for site in served if funnel.get(site) is True]


def load_status(path=None, run=None):
    if path is not None:
        try:
            return json.loads(Path(path).read_text())
        except (OSError, ValueError) as e:
            raise StatusError(f"cannot read status file {path}: {e}") from e
    try:
        done = (run or subprocess.run)(STATUS_ARGV, capture_output=True, text=True, timeout=30, check=False)
    except OSError as e:
        raise StatusError(f"tailscale serve status failed: {e}") from e
    if done.returncode != 0:
        raise StatusError(f"tailscale serve status failed with exit {done.returncode}: {done.stderr.strip()}")
    try:
        return json.loads(done.stdout)
    except ValueError as e:
        raise StatusError(f"tailscale serve status failed: output is not JSON: {e}") from e
