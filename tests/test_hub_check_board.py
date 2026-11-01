"""A hub on a port other than the live one keeps its board cache and flow history out of the live board cache (#1000)."""
import importlib.util
import os
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "contrib/squad-goal/scripts"


def _profile(d: Path, slug: str, repo: str, checkout: Path) -> None:
    owner = repo.split("/")[0]
    (d / f"{slug}.sh").write_text(
        f': "${{SG_REPO:={repo}}}"; : "${{SG_OWNER:={owner}}}"; : "${{SG_PROJECT:=3}}"; : "${{SG_CHECKOUT:={checkout}}}"\n')


def _apps(tmp_path, monkeypatch):
    prof, base = tmp_path / "profiles", tmp_path / "state"
    prof.mkdir()
    (tmp_path / "co-alpha").mkdir()
    _profile(prof, "alpha", "x/alpha", tmp_path / "co-alpha")
    monkeypatch.setenv("SG_PROFILES_DIR", str(prof))
    monkeypatch.setenv("SG_STATE_BASE", str(base))
    spec = importlib.util.spec_from_file_location("hubmux_check_under_test", SCRIPTS / "hubmux.py")
    mux = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mux)
    return mux, base


def test_a_check_hub_on_another_port_never_takes_the_live_board_cache(tmp_path, monkeypatch):
    mux, base = _apps(tmp_path, monkeypatch)
    mux.PORT = 7799
    board = mux.load_apps()["alpha"]["mod"].BOARD
    assert not board.is_relative_to(base / "_boards"), board


def test_the_live_hub_on_the_default_port_keeps_the_profile_board_cache(tmp_path, monkeypatch):
    mux, base = _apps(tmp_path, monkeypatch)
    board = mux.load_apps()["alpha"]["mod"].BOARD
    assert board == base / "_boards" / "x_3" / "hub-board.json", board


def test_the_test_session_never_takes_the_machine_state_base():
    base = os.environ.get("SG_STATE_BASE", "")
    assert base and Path(base) != Path.home() / ".cache" / "squad-goal", base
