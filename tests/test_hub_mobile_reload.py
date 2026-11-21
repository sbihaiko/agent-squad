"""The phone reloads on a new hub build, and holds the reload while work is open (#1455, part of #1115).

The page is the served phone page, booted under node over the shared stub DOM. The driver calls the page's own
`checkVersion`, which the page's interval calls every 3 s as the desktop does, and counts reloads by replacing the stub's
`location.reload`. The worker's cache name carries the build.
"""
import json
import re

import pytest

from tests import hub_mobile_stub as move

hub = move.hub
run_page = move.run_page

# The build is a nanosecond count, past the 2**53 a JavaScript number keeps exactly: a one-second gap is a real new build.
NEW_BUILD_GAP = 10**9


def version_route(hub, build):
    return {"/api/version": [{"status": 200, "ct": "application/json", "body": json.dumps({"v": build})}]}


def reloads_after(hub, driver, build):
    return run_page(hub.mobile_html().decode(), driver, routes=version_route(hub, build))


@pytest.mark.node_bound
def test_a_new_build_reloads_the_phone_exactly_once(hub):
    out = reloads_after(hub, """
      let reloads = 0; location.reload = () => { reloads++; };
      await checkVersion(); await checkVersion(); await checkVersion();
      return { reloads };
    """, hub.BOOT + NEW_BUILD_GAP)
    assert out["reloads"] == 1, out


@pytest.mark.node_bound
def test_the_same_build_never_reloads_the_phone(hub):
    out = reloads_after(hub, """
      let reloads = 0; location.reload = () => { reloads++; };
      await checkVersion(); await checkVersion();
      return { reloads };
    """, hub.BOOT)
    assert out["reloads"] == 0, out


# The reload holds while the page has a write open, and while the page's own promote request record stands (#2501).
HOLDS = {
    "a write in flight": (
        """
        let release;
        window.__held = writing(new Promise(r => { release = () => r({ ok: true, status: 200,
          headers: { get: () => "application/json" }, json: async () => ({}), text: async () => "{}" }); }));
        """,
        "release(); await __tick(30);",
    ),
    "a promote request waiting": (
        "promoReqs[7] = { phase: 'requested' };",
        "delete promoReqs[7];",
    ),
}


@pytest.mark.node_bound
@pytest.mark.parametrize("hold", sorted(HOLDS))
def test_the_reload_waits_for_the_hold_and_follows_when_it_clears(hub, hold):
    setup, clear = HOLDS[hold]
    out = reloads_after(hub, f"""
      let reloads = 0; location.reload = () => {{ reloads++; }};
      {setup}
      await checkVersion();
      const held = reloads;
      {clear}
      await checkVersion();
      return {{ held, after: reloads }};
    """, hub.BOOT + NEW_BUILD_GAP)
    assert out["held"] == 0, f"the phone reloaded while {hold}: {out}"
    assert out["after"] == 1, f"the reload did not follow once {hold} cleared: {out}"


@pytest.mark.node_bound
def test_a_reload_reads_no_removed_composer_and_logs_no_error(hub):
    """The composer left the page with the chat (#2490): a boot reads no composer and the page opens no draft."""
    out = reloads_after(hub, """
      let errors = 0;
      console.error = () => { errors++; };
      await __tick(20);
      return { text: !!document.getElementById("termview-text"), draft: typeof termDraft,
               attach: !!document.getElementById("attach-sheet"), errors };
    """, hub.BOOT)
    assert out == {"text": False, "draft": "undefined", "attach": False, "errors": 0}, out


@pytest.mark.node_bound
def test_the_cache_name_carries_the_build(hub, monkeypatch):
    def cache_name():
        return re.search(rb'const CACHE = "([^"]+)"', hub.mobile_sw()).group(1).decode()

    monkeypatch.setattr(hub, "BOOT", 111)
    first = cache_name()
    monkeypatch.setattr(hub, "BOOT", 222)
    second = cache_name()
    assert first == "squad-hub-shell-111", first
    assert second == "squad-hub-shell-222", "the cache name does not change with the build"
