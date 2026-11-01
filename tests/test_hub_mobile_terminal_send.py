"""The mobile composer keeps its draft and send feedback per session, and one message is one request (#1006).

The served page is booted under node over the stub DOM of `tests/test_hub_mobile_move.py`; the send is proved by the
requests the page posts, never by a substring of its text.
"""
import json
import os
import tempfile

import pytest

from tests.test_hub_mobile_move import HUB_PY, _module, run_page

A = "11111111-1111-1111-1111-111111111111"
B = "22222222-2222-2222-2222-222222222222"
EMPTY = {"body": json.dumps({"turns": [], "next": None}), "status": 200}


def reply(status):
    return {"body": json.dumps({"status": status, "reason": ""}), "status": 200}


def routes(*sends):
    return {"/api/sessions/conversation": [EMPTY], "/api/sessions/send": list(sends) or [reply("sent")]}


PRELUDE = """
  let seq = 0;
  globalThis.crypto = { randomUUID: () => "id-" + (++seq) };
  const send = $("termview-send"), text = $("termview-text"), out = $("termview-sent");
  const ids = () => __state.posts.map(p => JSON.parse(p.opts.body).client_id);
"""


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ.setdefault("SG_CHECKOUT", tempfile.mkdtemp())
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    return _module("hub_mobile_terminal_send", HUB_PY)


@pytest.mark.node_bound
def test_the_draft_and_the_send_feedback_stay_with_their_session(hub):
    out = run_page(hub.MOBILE_PAGE, PRELUDE + f"""
      openConversation("{A}", "a");
      text.value = "hello A";
      await send.onclick();
      const aSent = out.textContent;
      text.value = "half typed A";
      openConversation("{B}", "b");
      const b = {{ text: text.value, sent: out.textContent }};
      text.value = "draft B";
      openConversation("{A}", "a");
      const a = {{ text: text.value, sent: out.textContent }};
      closeConversation();
      openConversation("{B}", "b");
      return {{ aSent, a, b, b2: {{ text: text.value, sent: out.textContent }} }};
    """, routes=routes(reply("uncertain")))
    assert out["aSent"].startswith("Uncertain")
    assert out["b"] == {"text": "", "sent": ""}, "session B shows session A's draft or send feedback"
    assert out["a"] == {"text": "half typed A", "sent": out["aSent"]}
    assert out["b2"] == {"text": "draft B", "sent": ""}


@pytest.mark.node_bound
def test_send_is_disabled_while_its_request_is_pending_and_a_second_tap_posts_nothing(hub):
    out = run_page(hub.MOBILE_PAGE, PRELUDE + f"""
      openConversation("{A}", "a");
      text.value = "once";
      const first = send.onclick();
      const during = send.disabled;
      const second = send.onclick();
      openConversation("{B}", "b");
      const other = send.disabled;
      openConversation("{A}", "a");
      const back = send.disabled;
      await first; await second; await __tick(10);
      return {{ during, other, back, after: send.disabled, posts: __state.posts.length }};
    """, routes=routes(reply("sent")))
    assert out["posts"] == 1, out
    assert out["during"] is True and out["back"] is True
    assert out["other"] is False, "a pending send in session A disables the send of session B"
    assert out["after"] is False


@pytest.mark.node_bound
def test_a_retry_reuses_the_request_id_and_a_new_message_takes_a_new_one(hub):
    out = run_page(hub.MOBILE_PAGE, PRELUDE + f"""
      openConversation("{A}", "a");
      text.value = "retry me";
      await send.onclick();
      await send.onclick();
      const cleared = text.value;
      text.value = "next";
      await send.onclick();
      text.value = "refused one";
      await send.onclick();
      await send.onclick();
      return {{ ids: ids(), cleared }};
    """, routes=routes(reply("uncertain"), reply("sent"), reply("sent"), reply("refused"), reply("refused")))
    ids = out["ids"]
    assert ids[0] == ids[1], "a retry of an uncertain send takes a new request id"
    assert ids[2] != ids[1] and out["cleared"] == ""
    assert ids[3] != ids[2] and ids[4] != ids[3], "a refused send is final: the next tap is a new request"


@pytest.mark.node_bound
def test_a_retry_after_a_network_failure_reuses_the_request_id(hub):
    out = run_page(hub.MOBILE_PAGE, PRELUDE + f"""
      openConversation("{A}", "a");
      text.value = "flaky";
      await send.onclick();
      await send.onclick();
      text.value = "flaky, edited";
      await send.onclick();
      return {{ ids: ids() }};
    """, routes=routes({"body": "{}", "status": 502}, {"body": "{}", "status": 502}, reply("sent")))
    ids = out["ids"]
    assert ids[0] == ids[1], "a retry after the hub failed takes a new request id"
    assert ids[2] != ids[1], "an edited message is a new request"
