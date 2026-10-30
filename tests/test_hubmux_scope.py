"""The mux pins each profile's roots at import, so one profile never lends its run roots to another (#859).

`hub.py` reads SG_PROJECTS and SG_FORK_DIR once, at import, from the context the mux pins for that profile. The shell that
starts the mux may set its own SG_FORK_DIR (the agent-squad profile does); no other profile may see it. Fixture profiles
over temp checkouts; no network.
"""
import importlib.util
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "contrib/squad-goal/scripts"


@pytest.fixture
def two_profiles(tmp_path, monkeypatch):
    prof, base = tmp_path / "profiles", tmp_path / "state"
    prof.mkdir()
    fork_a, fork_shell = tmp_path / "fork-a", tmp_path / "fork-shell"   # fork_a is alpha's; fork_shell is the shell's
    for co in (tmp_path / "co-alpha", tmp_path / "co-beta", fork_a, fork_shell):
        (co / "runs").mkdir(parents=True)
    (prof / "alpha.sh").write_text(
        f': "${{SG_REPO:=x/alpha}}"; : "${{SG_OWNER:=x}}"; : "${{SG_PROJECT:=3}}"; : "${{SG_CHECKOUT:={tmp_path}/co-alpha}}"\n'
        f'export SG_FORK_DIR="{fork_a}"\n')
    (prof / "beta.sh").write_text(
        f': "${{SG_REPO:=y/beta}}"; : "${{SG_OWNER:=y}}"; : "${{SG_PROJECT:=4}}"; : "${{SG_CHECKOUT:={tmp_path}/co-beta}}"\n')
    monkeypatch.setenv("SG_PROFILES_DIR", str(prof))
    monkeypatch.setenv("SG_STATE_BASE", str(base))
    monkeypatch.setenv("SG_FORK_DIR", str(fork_shell))   # the mux's own shell, which no profile may inherit
    spec = importlib.util.spec_from_file_location("hubmux_scope", SCRIPTS / "hubmux.py")
    mux = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mux)
    return mux, mux.load_apps(), fork_a, fork_shell


def test_a_profile_sees_its_own_fork_dir_and_not_the_shells(two_profiles):
    mux, apps, fork_a, fork_shell = two_profiles
    assert "err" not in apps["alpha"] and "err" not in apps["beta"]
    own_alpha = set(apps["alpha"]["mod"].own_roots())
    assert set(apps["alpha"]["mod"].run_roots(fork_a)) <= own_alpha


def test_a_profile_without_a_fork_dir_never_borrows_the_shells(two_profiles):
    mux, apps, fork_a, fork_shell = two_profiles
    own_beta = set(apps["beta"]["mod"].own_roots())
    assert not (set(apps["beta"]["mod"].run_roots(fork_shell)) & own_beta)
    assert not (set(apps["beta"]["mod"].run_roots(fork_a)) & own_beta)
