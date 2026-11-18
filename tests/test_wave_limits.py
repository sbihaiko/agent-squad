"""The wave's age cap and the child attribution of a red wave check (#377, #378, ADR-0145 rules 11 and 13).

Issue #378: a wave has an age cap, a profile setting, two hours by default (`SG_WAVE_MAX_AGE_SECONDS`). The default lives
in `contrib/squad-goal/scripts/env.sh` beside the profile, so a profile that names the setting replaces it and one that
does not keeps the two hours. The two cases below read the real `env.sh` under a throwaway profile directory
(`SG_PROFILES_DIR`), so the default and the override are read from the code the loop runs, never from a copy.

Issue #377: a red check at the wave tip names the child that broke it. The wave's check is its CI in production, which
GitHub Actions cannot run while the account's billing is blocked (issue #439); the local check at the tip
(`SG_WAVE_TIP_GATES`) is the same rule and is what `test-wave.sh` runs today. Besides the profile cases, this file holds
that suite to the cases it claims, in the same shape `tests/test_wave_land.py` does (#375): a green suite that never
reached a case is the one way a suite can lie.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tests import shell_suites

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "contrib" / "squad-goal" / "scripts"
ENV_SH = SCRIPTS / "env.sh"
# The wave cases of #377 and #378, in the suite's own words: a wave old past its cap lands what it holds and requeues the
# rest, an expired wave takes no new child, and a red tip names the child that broke it and lands without it.
WAVE_CASES = (
    "L: the run's wave records its start for the age cap",
    "L: a wave younger than its cap is not expired",
    "L: a wave past its cap is expired",
    "L: an expired wave takes no child with a stale approval, and its issue is back in the queue",
    "L: the child with a stale approval does not enter",
    "L: a cap requeue does not count toward the requeue cap",
    "L: an approved child still enters an expired wave",
    "L: the expired wave lands what it holds (exit 0)",
    "L: the child the wave held is in dev",
    "L: the card of the held child reads Closed",
    "L: the held child's issue is closed",
    "L: the approved late child is in dev",
    "L: the child still running is back in the queue",
    "L: the landing's cap requeue does not count toward the requeue cap",
    "L: the child back in the queue is not closed",
    "L: the landed wave clears its start marker",
    "M: the wave without the child that broke it lands (exit 0)",
    "M: the red check names the child that broke it",
    "M: the rest of the wave is in dev",
    "M: the child that broke the wave did not land",
    "M: the issue of the rest is closed",
    "M: the issue of the child that broke the wave is not closed",
    "M: the child that broke the wave is back in the queue",
    "M: the wave branch goes with its landing",
)


def _cap(tmp_path: Path, profile_body: str) -> str:
    """The wave age cap the real env.sh resolves for a throwaway profile with the given body."""
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    (profiles / "agent-squad.sh").write_text(
        ': "${SG_REPO:=owner/repo}"\n: "${SG_OWNER:=owner}"\n: "${SG_PROJECT:=1}"\n'
        ': "${SG_CHECKOUT:=$HOME/co}"\n: "${SG_BASE:=dev}"\n' + profile_body
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith("SG_")}
    env.update(SG_PROFILES_DIR=str(profiles), SG_PROFILE="agent-squad",
               SG_STATE_BASE=str(tmp_path / "cache"), SG_CHECKOUT=str(tmp_path / "co"))
    script = f'. "{ENV_SH}" >/dev/null 2>&1 || exit 2; printf "%s\\n" "${{SG_WAVE_MAX_AGE_SECONDS:-unset}}"'
    r = subprocess.run(["bash", "-c", script], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout.strip()


def test_the_wave_age_cap_defaults_to_two_hours(tmp_path: Path) -> None:
    assert _cap(tmp_path, "") == "7200", "the wave's age cap does not default to two hours (7200 s)"


def test_a_profile_can_change_the_wave_age_cap(tmp_path: Path) -> None:
    assert _cap(tmp_path, ': "${SG_WAVE_MAX_AGE_SECONDS:=600}"\n') == "600"


@pytest.mark.slow(reason="runs the wave's hermetic shell suite over throwaway repositories, more than 20 s on a loaded machine")
@pytest.mark.heavy
def test_the_wave_suite_proves_the_age_cap_and_the_red_tip() -> None:
    r = shell_suites.run(SCRIPTS / "test-wave.sh")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "TDD GREEN" in r.stdout, r.stdout
    for case in WAVE_CASES:
        assert f"ok   {case}" in r.stdout, f"{case} did not pass in the wave suite:\n{r.stdout}"
