"""A hub that reads a board cache older than the profile's own says so on the page (#1219).

hubmux.py pins SG_BOARD_CACHE for the live hub. A hub started before that pin read runs/.hub/hub-board.json and served a
two-day-old board while the profile's own cache was current. The hub compares its cache with the one the seam names
(address.sh `sg_board_cache`) and, past SG_HUB_BOARD_STALE_SECONDS, reports it through the payload's one error key (`err`,
which the page already shows for a failed board read). A gap inside the bound is no error, and a failed read keeps its own.
"""
import importlib.util
import os
import re
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"
NOW = 1_800_000_000


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ.setdefault("SG_CHECKOUT", tempfile.mkdtemp())
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_board_stale", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _cache(hub, monkeypatch, err=None):
    """A board cache with no rows: the box, the lane and the summary still build."""
    monkeypatch.setattr(hub, "board_cache", {"t": 1.0, "items": [], "prs": [], "lc": {}, "err": err, "errs": []})
    monkeypatch.setattr(hub, "read_moves", lambda: {})


def _pair(tmp_path, monkeypatch, hub, gap):
    """The board the hub reads and the profile's own cache, the second `gap` seconds newer. Returns both paths."""
    served = tmp_path / "served" / "hub-board.json"
    own = tmp_path / "profile" / "hub-board.json"
    served.parent.mkdir(parents=True, exist_ok=True)
    own.parent.mkdir(parents=True, exist_ok=True)
    served.write_text("{}")
    own.write_text("{}")
    os.utime(served, (NOW, NOW))
    os.utime(own, (NOW + gap, NOW + gap))
    monkeypatch.setattr(hub, "BOARD", served)
    monkeypatch.setattr(hub, "profile_board_cache", lambda: own)
    return served, own


def test_a_board_cache_older_than_the_profiles_own_is_named_in_err(hub, tmp_path, monkeypatch):
    _cache(hub, monkeypatch)
    _pair(tmp_path, monkeypatch, hub, gap=7200)
    monkeypatch.setenv("SG_HUB_BOARD_STALE_SECONDS", "600")
    assert "older" in (hub.board()["err"] or "")


def test_a_gap_inside_the_named_bound_is_no_error(hub, tmp_path, monkeypatch):
    _cache(hub, monkeypatch)
    _pair(tmp_path, monkeypatch, hub, gap=120)
    monkeypatch.setenv("SG_HUB_BOARD_STALE_SECONDS", "600")
    assert hub.board()["err"] is None


def test_the_bound_is_the_named_setting(hub, tmp_path, monkeypatch):
    _cache(hub, monkeypatch)
    _pair(tmp_path, monkeypatch, hub, gap=120)
    monkeypatch.setenv("SG_HUB_BOARD_STALE_SECONDS", "60")
    assert "older" in (hub.board()["err"] or "")


def test_a_hub_that_reads_the_profiles_own_cache_says_nothing(hub, tmp_path, monkeypatch):
    _cache(hub, monkeypatch)
    own = tmp_path / "own" / "hub-board.json"
    own.parent.mkdir(parents=True, exist_ok=True)
    own.write_text("{}")
    monkeypatch.setattr(hub, "BOARD", own)
    monkeypatch.setattr(hub, "profile_board_cache", lambda: own)
    monkeypatch.setenv("SG_HUB_BOARD_STALE_SECONDS", "0")
    assert hub.board()["err"] is None


def test_a_missing_profile_cache_is_not_stale(hub, tmp_path, monkeypatch):
    _cache(hub, monkeypatch)
    served = tmp_path / "served" / "hub-board.json"
    served.parent.mkdir(parents=True, exist_ok=True)
    served.write_text("{}")
    monkeypatch.setattr(hub, "BOARD", served)
    monkeypatch.setattr(hub, "profile_board_cache", lambda: tmp_path / "absent" / "hub-board.json")
    assert hub.board()["err"] is None


def test_a_failed_read_keeps_its_own_err_over_the_staleness(hub, tmp_path, monkeypatch):
    _cache(hub, monkeypatch, err="items read failed")
    _pair(tmp_path, monkeypatch, hub, gap=7200)
    monkeypatch.setenv("SG_HUB_BOARD_STALE_SECONDS", "600")
    assert hub.board()["err"] == "items read failed"


def test_the_served_board_names_a_legacy_cache_the_seam_resolves(hub, tmp_path, monkeypatch):
    """The defect's shape: the hub reads a legacy runs/.hub cache, and the seam names a current per-profile cache (#1219)."""
    _cache(hub, monkeypatch)
    legacy = tmp_path / "runs" / ".hub" / "hub-board.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("{}")
    os.utime(legacy, (NOW, NOW))
    monkeypatch.setattr(hub, "BOARD", legacy)
    monkeypatch.setattr(hub, "_PROFILE_BOARD", None)
    monkeypatch.delenv("SG_BOARD_CACHE", raising=False)
    monkeypatch.setenv("SG_STATE_BASE", str(tmp_path / "state_base"))
    own = Path(os.environ["SG_STATE_BASE"]) / "_boards" / f"{hub.OWNER}_{hub.PROJECT_NO}" / "hub-board.json"
    own.parent.mkdir(parents=True)
    own.write_text("{}")
    os.utime(own, (NOW + 7200, NOW + 7200))
    assert own == hub.profile_board_cache()
    assert "older" in (hub.board()["err"] or "")


def test_the_profile_cache_is_the_one_the_seam_names(hub, monkeypatch):
    """With SG_BOARD_CACHE unset, the seam's sg_board_cache names the profile's own file, and the hub reads that same path."""
    monkeypatch.setattr(hub, "_PROFILE_BOARD", None)
    monkeypatch.delenv("SG_BOARD_CACHE", raising=False)
    monkeypatch.setenv("SG_STATE_BASE", str(Path(tempfile.mkdtemp())))
    own = hub.profile_board_cache()
    assert own == Path(os.environ["SG_STATE_BASE"]) / "_boards" / f"{hub.OWNER}_{hub.PROJECT_NO}" / "hub-board.json"


# Staleness has one form on every surface (#1137): one caption with a number and a unit, on one line per page in mute,
# which turns warn and reads "(stale)" past the hub's threshold. The hub decides the threshold (the payload's one error
# key, proved above); a page maps no fact of its own. Stale content is dimmed and desaturated in one way.

def test_the_hub_page_names_staleness_on_one_mute_line_that_turns_warn(hub):
    css = hub.PAGE.split("<style>")[1].split("</style>")[0]
    assert re.search(r"#bread\{[^}]*color:var\(--mute\)", css)
    assert re.search(r"#bread\.stale\{[^}]*color:var\(--warn\)", css)
    dim = re.search(r"#bbody\.stale\{([^}]*)\}", css)
    assert dim and "opacity" in dim.group(1) and "filter" in dim.group(1), "stale content is dimmed and desaturated in one way"
    page = hub.PAGE.split("<script>")[-1]
    assert 'classList.toggle("stale",readStale)' in page
    assert '(b.err?"(stale) ":"")' not in page, "one staleness line per page: the board head carries no second stale marker"


def test_the_phone_page_names_staleness_on_one_mute_line_that_turns_warn(hub):
    mobile = hub.MOBILE_PAGE.decode() if isinstance(hub.MOBILE_PAGE, bytes) else hub.MOBILE_PAGE
    css = mobile.split("<style>")[1].split("</style>")[0]
    assert re.search(r"#bread\{[^}]*color:var\(--mute\)", css)
    assert re.search(r"#bread\.stale\{[^}]*color:var\(--warn\)", css)
    dim = re.search(r"#cards\.stale\{([^}]*)\}", css)
    assert dim and "opacity" in dim.group(1) and "filter" in dim.group(1), "stale content is dimmed and desaturated in one way"
    assert re.search(r'board read \$\{[^}]*\} s ago', mobile)
    assert 'classList.toggle("stale",boardStale)' in mobile
    assert 'b.err ? "(stale)"' not in mobile, "one staleness line per page: the message line keeps errors only"


def test_the_dashboard_names_staleness_on_one_mute_line_that_turns_warn(hub):
    from tests.dashboard_helpers import PAGE_SRC
    src = PAGE_SRC()
    assert 'stale ? " (stale)"' in src
    assert "No new event for over" not in src, "one staleness line per page: the stalled note is not a second line"
    css = src.split("<style>")[1].split("</style>")[0]
    assert re.search(r"\.note\.stale\{[^}]*color:var\(--warn\)", css)
    dim = re.search(r"\.runbox\.stale\{([^}]*)\}", css)
    assert dim and "opacity" in dim.group(1) and "filter" in dim.group(1), "stale content is dimmed and desaturated in one way"
