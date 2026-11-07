"""#1373: a streamed no-tools review answer is read into the CLI's JSON shape, with its usage; a cut stream is no answer."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STREAM = ROOT / "contrib/squad-goal/scripts/review_stream.py"


def _sse(*events):
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)


def _read(sse):
    out = subprocess.run([sys.executable, str(STREAM)], input=sse, capture_output=True, text=True, check=True).stdout
    return json.loads(out)


START = {"type": "message_start", "message": {"usage": {"input_tokens": 900, "output_tokens": 1, "cache_read_input_tokens": 300}}}
TEXT = [{"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "VERDICT: "}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "APPROVE"}}]
DELTA = {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 42}}
STOP = {"type": "message_stop"}


def test_a_complete_stream_is_the_text_with_its_usage():
    got = _read(_sse(START, *TEXT, DELTA, STOP))
    assert got == {"result": "VERDICT: APPROVE", "usage": {"input_tokens": 900, "output_tokens": 42, "cache_read_input_tokens": 300},
                   "subtype": "success", "is_error": False}


def test_a_keepalive_between_the_events_is_no_answer_and_no_error():
    got = _read(_sse(START, {"type": "ping"}, *TEXT, {"type": "ping"}, DELTA, STOP))
    assert (got["result"], got["subtype"], got["is_error"]) == ("VERDICT: APPROVE", "success", False)


def test_a_stream_cut_before_its_stop_is_an_error_that_keeps_the_usage_it_read():
    got = _read(_sse(START, *TEXT))
    assert got["is_error"] is True and got["subtype"] == "error_stream"
    assert got["usage"]["input_tokens"] == 900


def test_an_empty_stream_is_an_error():
    got = _read("")
    assert got["is_error"] is True and got["subtype"] == "error_stream" and got["result"] == ""


def test_an_error_event_names_its_error_type():
    err = {"type": "error", "error": {"type": "overloaded_error", "message": "busy"}}
    got = _read(_sse(START, err))
    assert got["is_error"] is True and got["subtype"] == "error_overloaded_error"


def test_an_answer_cut_at_max_tokens_is_an_error():
    cut = {"type": "message_delta", "delta": {"stop_reason": "max_tokens"}, "usage": {"output_tokens": 8000}}
    got = _read(_sse(START, *TEXT, cut, STOP))
    assert got["is_error"] is True and got["subtype"] == "error_max_tokens"


def test_a_non_data_line_and_a_malformed_data_line_are_skipped():
    body = "event: message_start\n: comment\n" + f"data: {json.dumps(START)}\n\n" + "data: {not json}\n\n" \
        + f"data: {json.dumps(DELTA)}\n\ndata: {json.dumps(STOP)}\n\n"
    got = _read(body)
    assert (got["subtype"], got["is_error"], got["usage"]["input_tokens"]) == ("success", False, 900)
