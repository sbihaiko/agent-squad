"""The session overlay draws the turns the push stream pushes (SPEC 1085, #1097): a turn arrives with no poll tick, is drawn
once, and the view keeps the line the reader reads. The rules are pure functions of `hub-overlay.js`, run under node; the
wiring is read from the page's source. The bubble markup is locked by test_hub_overlay.py.
"""
import shutil

import pytest

from tests.test_hub_overlay import OVERLAY, run_js

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is needed for the overlay rules")


def test_a_stream_line_is_one_turn_and_a_turn_is_never_drawn_twice():
    out = run_js("""(() => {
      const drawn = [{k: "0:0", id: "a", text: "first"}, {k: "0:1", id: "b", text: "second"}];
      const pushed = SQ.streamTurns('{"k":"0:1","id":"b","text":"second"}\\n{"k":"0:2","id":"c","text":"third"}\\n');
      const merged = SQ.mergeTurns(drawn, pushed);
      return {pushed: pushed.map(t => t.k), keys: merged.map(t => t.k), texts: merged.map(t => t.text)};
    })()""")
    assert out == {"pushed": ["0:1", "0:2"], "keys": ["0:0", "0:1", "0:2"], "texts": ["first", "second", "third"]}, out


def test_a_reader_that_resumes_draws_the_window_once_and_in_order():
    out = run_js("""(() => {
      const held = [{k: "1:0"}, {k: "1:1"}];
      const resumed = SQ.streamTurns('{"k":"1:1"}\\n{"k":"1:2"}\\n{"k":"1:3"}\\n');
      return SQ.mergeTurns(SQ.mergeTurns(held, resumed), resumed).map(t => t.k);
    })()""")
    assert out == ["1:0", "1:1", "1:2", "1:3"], out


def test_the_talk_reads_the_stream_only_while_the_stream_is_live_and_watched():
    out = run_js("[SQ.feedFrom(true, true), SQ.feedFrom(false, true), SQ.feedFrom(true, false), SQ.feedFrom(false, false)]")
    assert out == ["push", "poll", "poll", "poll"], out


def test_a_repaint_above_the_newest_line_keeps_the_line_the_reader_reads():
    out = run_js("[SQ.viewAfter(100, 130, 500), SQ.viewAfter(130, 100, 500), SQ.viewAfter(null, null, 500)]")
    assert out == [530, 470, 500], out


def test_the_overlay_watches_its_session_and_draws_the_pushed_turns_with_no_tick():
    src = OVERLAY.read_text()
    assert 'squadStream.on("conversation"' in src
    assert 'squadStream.on("gap"' in src
    assert "squadStream.watch(" in src and "squadStream.unwatch(" in src
    assert "SQ.feedFrom(" in src and "SQ.mergeTurns(" in src and "SQ.viewAfter(" in src
