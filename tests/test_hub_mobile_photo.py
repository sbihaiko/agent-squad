"""The phone's one photo (#1155): the composer's one image input offers the camera and the library, a picked image
previews from the hub, and the send takes the overlay's one attach route. The page is read as source and booted under
node over the stub DOM of `tests/test_hub_mobile_move.py`; the content-security line is read from the served header.
"""
import http.client
import json
import os
import re
import tempfile
import threading
from http.server import ThreadingHTTPServer

import pytest

from tests.test_hub_mobile_move import HUB_PY, _module, run_page

SID = "11111111-1111-1111-1111-111111111111"
OTHER = "22222222-2222-2222-2222-222222222222"
CID = "11111111-1111-4111-8111-111111111111"
CAP = 4000
EMPTY = {"body": json.dumps({"turns": [], "next": None}), "status": 200}


def reply(status):
    return {"body": json.dumps({"status": status, "reason": ""}), "status": 200}


def routes(*answers):
    return {"/api/sessions/conversation": [EMPTY],
            "/api/sessions/attach": list(answers) or [reply("sent")]}


PRELUDE = """
  globalThis.crypto = { randomUUID: () => "__CID__" };
  const revoked = [];
  globalThis.URL = { createObjectURL: () => "blob:hub/photo", revokeObjectURL: u => revoked.push(u) };
  const pick = async () => {
    const picker = $("termview-file");
    picker.files = [{ name: "camera.jpg", arrayBuffer: async () => new Uint8Array([255, 216, 255, 65]).buffer }];
    picker.listeners.change[0]({ target: picker });
    await __tick(20);
  };
  const queryOf = url => {
    const out = {};
    for (const part of (url.split("?")[1] || "").split("&").filter(Boolean)) {
      const [k, v] = part.split("=");
      out[decodeURIComponent(k)] = decodeURIComponent((v || "").replace(/\\+/g, " "));
    }
    return out;
  };
""".replace("__CID__", CID)


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ.setdefault("SG_CHECKOUT", tempfile.mkdtemp())
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    return _module("hub_mobile_photo", HUB_PY)


@pytest.fixture
def served(hub, monkeypatch):
    monkeypatch.setattr(hub, "run_list", lambda: [])
    monkeypatch.setattr(hub, "_loop_on", lambda pid_file=None: False)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), hub.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_port
    srv.shutdown()
    srv.server_close()


def test_the_composer_has_one_image_input_that_offers_the_camera_and_the_library(hub):
    inputs = re.findall(r"<input\b[^>]*>", hub.MOBILE_PAGE)
    files = [tag for tag in inputs if re.search(r"(?<![\w-])type=[\"']?file\b", tag)]
    assert len(files) == 1, f"the phone composer holds {len(files)} file inputs, not one"
    assert re.search(r"(?<![\w-])accept=[\"']image/\*[\"']", files[0]), files[0]
    assert not re.search(r"(?<![\w-])capture\b", files[0]), "a capture attribute skips the library"


def test_the_picker_is_a_label_for_that_one_input(hub):
    page = hub.MOBILE_PAGE
    ident = re.search(r"(?<![\w-])id=[\"']?([\w-]+)", re.search(r"<input\b[^>]*type=[\"']?file\b[^>]*>", page).group(0)).group(1)
    assert re.search(rf"<label\b[^>]*\bfor=[\"']?{re.escape(ident)}[\"']?[^>]*>[^<]+</label>", page)


@pytest.mark.node_bound
def test_a_picked_image_previews_from_the_hub_and_sends_to_the_open_session(hub):
    out = run_page(hub.MOBILE_PAGE, PRELUDE + """
      openConversation("__SID__", "a");
      await __tick(10);
      const send = $("termview-send");
      await pick();
      const chip = { hidden: $("termview-attach").hidden, src: $("termview-attach-img").src,
                     name: $("termview-attach-name").textContent };
      $("termview-text").value = "Look at this";
      await send.onclick();
      const post = __state.posts.find(p => p.path === "/api/sessions/attach") || null;
      return { chip, status: $("termview-sent").textContent, text: $("termview-text").value,
               chipAfter: $("termview-attach").hidden, revoked,
               post: post && { method: post.opts.method, type: post.opts.headers["Content-Type"],
                               bytes: Array.from(new Uint8Array(post.opts.body)), query: queryOf(post.url) },
               textSends: __state.posts.filter(p => p.path === "/api/sessions/send").length };
    """.replace("__SID__", SID), routes=routes())
    assert out["chip"] == {"hidden": False, "src": "blob:hub/photo", "name": "camera.jpg"}, "the photo has no hub preview"
    assert out["post"] == {"method": "POST", "type": "application/octet-stream", "bytes": [255, 216, 255, 65],
                           "query": {"kind": "image", "sid": SID, "client_id": CID, "text": "Look at this"}}
    assert out["textSends"] == 0, "the photo took a second upload path"
    assert out["status"] == "Sent: the session's inbox accepted the message and the photo"
    assert out["text"] == "" and out["chipAfter"] is True and out["revoked"] == ["blob:hub/photo"]


@pytest.mark.node_bound
def test_removing_the_picked_photo_clears_the_chip_and_sends_nothing(hub):
    out = run_page(hub.MOBILE_PAGE, PRELUDE + """
      openConversation("__SID__", "a");
      await __tick(10);
      await pick();
      $("termview-attach-x").onclick();
      const cleared = $("termview-attach").hidden;
      $("termview-send").onclick();
      await __tick(10);
      return { cleared, revoked,
               attach: __state.posts.filter(p => p.path === "/api/sessions/attach").length,
               textSends: __state.posts.filter(p => p.path === "/api/sessions/send").length };
    """.replace("__SID__", SID), routes=routes())
    assert out["cleared"] is True, "a removed photo keeps its chip"
    assert out["revoked"] == ["blob:hub/photo"]
    assert out["attach"] == 0 and out["textSends"] == 0, "a removed photo reached the session"


@pytest.mark.node_bound
def test_a_photo_read_that_settles_after_a_switch_leaves_the_open_chip_alone(hub):
    """A read in flight belongs to the session that picked it (#1155). A slow read for A that settles while B is open
    must not repaint B's chip: only the open session owns the chip, and the read still lands on A's own draft."""
    out = run_page(hub.MOBILE_PAGE, PRELUDE + """
      const img = $("termview-attach-img");
      let writes = 0, cur = "";
      Object.defineProperty(img, "src", { configurable: true, get: () => cur, set: v => { writes++; cur = v; } });
      let release;
      const gate = new Promise(r => { release = r; });
      const pickSlow = () => {
        const picker = $("termview-file");
        picker.files = [{ name: "a.jpg", arrayBuffer: async () => { await gate; return new Uint8Array([1, 2, 3]).buffer; } }];
        picker.listeners.change[0]({ target: picker });
      };
      openConversation("__A__", "a");
      await __tick(10);
      pickSlow();
      await __tick(10);                      // A's read has not settled
      openConversation("__B__", "b");
      await __tick(10);
      await pick();                          // B's own photo settles and draws B's chip
      const painted = writes;
      release();                             // A's read settles while B is open
      await __tick(20);
      const after = writes;
      openConversation("__A__", "a");        // back to A: its read did land on its own draft
      await __tick(10);
      return { painted, after, aHidden: $("termview-attach").hidden, aName: $("termview-attach-name").textContent };
    """.replace("__A__", SID).replace("__B__", OTHER), routes=routes())
    assert out["after"] == out["painted"], f"a read for another session repainted the open chip: {out}"
    assert out["aHidden"] is False and out["aName"] == "a.jpg", f"A's own read did not land: {out}"


@pytest.mark.node_bound
def test_an_uncertain_photo_send_keeps_its_photo_and_reuses_its_request(hub):
    """An uncertain send is no answer (#1155): the photo stays, and the retry is the same request under the same client
    id, so the hub answers one message and never a text-only send that lost the photo."""
    answers = routes(reply("uncertain"), reply("sent"))
    answers["/api/sessions/send"] = [reply("sent")]   # a retry that lost its photo would be recorded here
    out = run_page(hub.MOBILE_PAGE, PRELUDE + """
      let n = 0; crypto.randomUUID = () => "cid-" + (++n);
      openConversation("__SID__", "a");
      await __tick(10);
      await pick();
      $("termview-text").value = "Look at this";
      await $("termview-send").onclick();
      const once = { hidden: $("termview-attach").hidden, name: (termDrafts.get("__SID__").photo || {}).name || null,
                     said: $("termview-sent").textContent, cid: termDrafts.get("__SID__").cid };
      await $("termview-send").onclick();
      const posts = __state.posts.filter(p => p.path === "/api/sessions/attach");
      return { once, said: $("termview-sent").textContent, hiddenAfter: $("termview-attach").hidden,
               cids: posts.map(p => queryOf(p.url).client_id), texts: posts.map(p => queryOf(p.url).text),
               bodies: posts.map(p => Array.from(new Uint8Array(p.opts.body))),
               textSends: __state.posts.filter(p => p.path === "/api/sessions/send").length };
    """.replace("__SID__", SID), routes=answers)
    assert out["once"]["hidden"] is False and out["once"]["name"] == "camera.jpg", f"an uncertain send dropped the photo: {out}"
    assert out["once"]["said"].startswith("Uncertain"), out
    assert len(out["cids"]) == 2 and out["cids"][0] == out["cids"][1] == out["once"]["cid"], f"the retry had a new request id: {out}"
    assert out["texts"] == ["Look at this", "Look at this"], out
    assert out["bodies"] == [[255, 216, 255, 65], [255, 216, 255, 65]], out
    assert out["textSends"] == 0, "the retry lost the photo and took the plain text route"
    assert out["hiddenAfter"] is True, "a sent photo keeps its chip"


@pytest.mark.node_bound
@pytest.mark.parametrize("given", [CAP, CAP + 5])
def test_a_photo_send_carries_at_most_the_phones_text_cap(hub, given):
    """The textarea's maxlength is the phone's one text cap (#1006): the send clamps to it too, so a photo message never
    carries more than 4000 characters, and a message at the cap is carried whole (#1155)."""
    out = run_page(hub.MOBILE_PAGE, PRELUDE + f"""
      openConversation("__SID__", "a");
      await __tick(10);
      await pick();
      $("termview-text").value = "x".repeat({given});
      await $("termview-send").onclick();
      const post = __state.posts.find(p => p.path === "/api/sessions/attach");
      return {{ text: queryOf(post.url).text, sent: $("termview-sent").textContent }};
    """.replace("__SID__", SID), routes=routes())
    assert out["text"] == "x" * CAP, f"{given} characters reached the attach route"


def test_the_text_cap_and_the_plain_text_send_are_unchanged(hub):
    page = hub.MOBILE_PAGE
    assert 'id="termview-text" maxlength="4000" rows="3"' in page
    assert 'fetch("/api/sessions/send", { method: "POST"' in page, "the plain text no longer takes the send route"


def test_the_content_security_line_admits_the_hub_preview_and_nothing_more(served):
    c = http.client.HTTPConnection("127.0.0.1", served, timeout=10)
    c.request("GET", "/mobile/", headers={"Host": f"127.0.0.1:{served}"})
    r = c.getresponse()
    csp = r.getheader("Content-Security-Policy") or ""
    r.read()
    c.close()
    assert r.status == 200
    assert csp == ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
                   "img-src 'self' blob:"), csp
    assert "*" not in csp and "http:" not in csp and "https:" not in csp and "data:" not in csp
