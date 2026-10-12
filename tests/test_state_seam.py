"""The state-directory seam (#20): one state directory per repository, one shared directory for what is machine-wide."""
import importlib.util
import pathlib
import subprocess
import sys

SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "contrib" / "squad-goal" / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


def _env(monkeypatch, **kw):
    for k in ("SG_STATE", "SG_STATE_BASE", "SG_SHARED", "SG_PROFILE"):
        monkeypatch.delenv(k, raising=False)
    for k, v in kw.items():
        monkeypatch.setenv(k, str(v))


def test_state_is_one_directory_per_profile_under_the_base(monkeypatch, tmp_path):
    sd = _load("statedir")
    _env(monkeypatch, SG_STATE_BASE=tmp_path, SG_PROFILE="alpha")
    assert sd.state() == tmp_path / "alpha"
    _env(monkeypatch, SG_STATE_BASE=tmp_path, SG_PROFILE="beta")
    assert sd.state() == tmp_path / "beta"


def test_a_profile_level_state_override_still_wins(monkeypatch, tmp_path):
    sd = _load("statedir")
    _env(monkeypatch, SG_STATE_BASE=tmp_path, SG_PROFILE="alpha", SG_STATE=tmp_path / "kept")
    assert sd.state() == tmp_path / "kept"


def test_the_shared_directory_is_one_for_every_profile_and_is_never_a_state_directory(monkeypatch, tmp_path):
    sd = _load("statedir")
    _env(monkeypatch, SG_STATE_BASE=tmp_path, SG_PROFILE="alpha"); a = sd.shared()
    _env(monkeypatch, SG_STATE_BASE=tmp_path, SG_PROFILE="beta"); b = sd.shared()
    assert a == b == tmp_path / "_shared"
    _env(monkeypatch, SG_STATE_BASE=tmp_path, SG_SHARED=tmp_path / "elsewhere")
    assert sd.shared() == tmp_path / "elsewhere"


def test_env_sh_gives_two_profiles_two_states_and_one_shared_directory(tmp_path):
    prof = tmp_path / "profiles"; prof.mkdir()
    for n in ("alpha", "beta"):
        (prof / f"{n}.sh").write_text(f': "${{SG_REPO:=x/{n}}}"; : "${{SG_OWNER:=x}}"; : "${{SG_PROJECT:=1}}"; : "${{SG_CHECKOUT:={tmp_path}/{n}}}"\n')
    seen = {}
    for n in ("alpha", "beta"):
        out = subprocess.run(["bash", "-c", '. "$1/env.sh" 2>/dev/null; echo "$SG_STATE $SG_SHARED"', "_", str(SCRIPTS)], capture_output=True, text=True,
                             env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "SG_PROFILES_DIR": str(prof), "SG_STATE_BASE": str(tmp_path / "st"),
                                  "SG_SQUAD": "/dev/null", "SG_PROFILE": n}).stdout.split()
        seen[n] = out
    assert seen["alpha"][0] == str(tmp_path / "st" / "alpha") and seen["beta"][0] == str(tmp_path / "st" / "beta")
    assert seen["alpha"][1] == seen["beta"][1] == str(tmp_path / "st" / "_shared")
    assert (tmp_path / "st" / "_shared").is_dir()



def _board(tmp_path, state, shared, repo):
    bindir = tmp_path / "bin"; bindir.mkdir(exist_ok=True); log = tmp_path / "gh.log"
    (bindir / "gh").write_text('#!/bin/bash\necho "$*" >> "LOGPATH"\ncase "$*" in\n'
        '  *users/x) echo \'{"type":"User"}\';;\n'
        '  *fields*) echo \'[{"name":"Status","id":"F1","options":[{"name":"Done","id":"O1"}]}]\';;\n'
        '  *items*--jq*) echo \'[7,"I7","https://api.github.com/repos/%s"]\';;\n'
        '  *) echo "{}";;\nesac\n'.replace("LOGPATH", str(log)).replace("%s", repo))
    (bindir / "gh").chmod(0o755)
    env = {"PATH": f"{bindir}:/usr/bin:/bin", "HOME": str(tmp_path), "SG_STATE": str(state), "SG_SHARED": str(shared), "SG_REPO": repo}
    r = subprocess.run([sys.executable, str(SCRIPTS / "board.py"), "x", "1", "Done", "7"], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    return log.read_text()


def test_the_board_cache_is_one_per_owner_and_project_in_the_shared_directory(tmp_path):
    shared = tmp_path / "shared"
    _board(tmp_path, tmp_path / "a", shared, "x/alpha")
    assert (shared / "rest-fields-x-1.json").exists() and (shared / "owner-kind-x.json").exists()
    assert not list((tmp_path / "a").glob("rest-fields-*")) and (tmp_path / "a" / "board-dirty").exists()
    (tmp_path / "gh.log").unlink()
    calls = _board(tmp_path, tmp_path / "b", shared, "x/beta")      # a second repository of the same project
    assert "/fields?" not in calls and not any(c.endswith(" users/x") for c in calls.splitlines()), "the second repository re-read what the first one cached"
    assert (tmp_path / "b" / "rest-items-x-1.json").exists() and not (shared / "rest-items-x-1.json").exists()
