"""The macOS probe workflow runs by hand, on a GitHub macOS runner, with the read-only token and no secret (#1976)."""
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/macos-probe.yml"


def _doc():
    return yaml.safe_load(WORKFLOW.read_text())


def _events(doc):
    on = doc.get(True, doc.get("on"))
    return {on} if isinstance(on, str) else set(on)


def test_the_probe_starts_by_hand_only():
    assert _events(_doc()) == {"workflow_dispatch"}


def test_the_probe_holds_the_read_only_token_at_every_level():
    doc = _doc()
    assert doc.get("permissions") == {"contents": "read"}
    for name, job in doc["jobs"].items():
        assert job.get("permissions", {"contents": "read"}) == {"contents": "read"}, name


def test_every_job_runs_on_a_github_macos_runner():
    for name, job in _doc()["jobs"].items():
        assert str(job["runs-on"]).startswith("macos-"), name


def test_the_probe_names_no_secret():
    assert not re.search(r"secrets\s*[.\[]", WORKFLOW.read_text())
