"""#521: the loop exports SG_BOARD_CACHE, and a gate it runs inherits it. hub.py reads it before its own SG_HUB_DIR, so a
hub test loaded the live board and failed on the loop's own landings. The test session drops it."""
import os

from tests.conftest import drop_live_paths


def test_the_session_drops_the_live_board_cache(monkeypatch):
    monkeypatch.setenv("SG_BOARD_CACHE", "/live/hub-board.json")
    drop_live_paths()
    assert "SG_BOARD_CACHE" not in os.environ, "a hub test module would read and write the loop's live board cache"
