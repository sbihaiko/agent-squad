"""The phone sheet closes from its grab handle (#1454).

A downward swipe that starts on the handle closes the sheet through the one close function. A drag that starts on the
handle never stages a move, even low in the screen where the 40% promotion band begins.
"""
import re

import pytest

from tests.test_hub_mobile_move import hub, run_page  # noqa: F401  (hub is a fixture)


@pytest.mark.node_bound
def test_a_downward_swipe_on_the_grab_handle_closes_the_sheet(hub):  # noqa: F811
    out = run_page(hub.MOBILE_PAGE, """
      await loadBoard();
      view = { ...view, sheet: true };
      renderSheet();
      $("sheet-content").scrollTop = 0;
      const grab = $("sheet-content").querySelector(".grab");
      __dispatch("touchstart", { target: grab, touches: [{ clientX: 100, clientY: 330 }] });
      __dispatch("touchend", { changedTouches: [{ clientX: 100, clientY: 420 }] });
      return { open: view.sheet, hidden: $("sheet").hidden };
    """)
    assert out == {"open": False, "hidden": True}, out


@pytest.mark.node_bound
def test_a_drag_that_starts_on_the_grab_handle_never_stages_a_move(hub):  # noqa: F811
    out = run_page(hub.MOBILE_PAGE, """
      await loadBoard();
      view = { ...view, sheet: true };
      renderSheet();
      $("sheet-content").scrollTop = 0;
      const grab = $("sheet-content").querySelector(".grab");
      __dispatch("touchstart", { target: grab, touches: [{ clientX: 0, clientY: 700 }] });
      __dispatch("touchend", { changedTouches: [{ clientX: 100, clientY: 700 }] });
      return { pending: pending ? pending.n : null, open: view.sheet };
    """)
    assert out["pending"] is None, out
    assert out["open"] is True, out


@pytest.mark.node_bound
def test_a_handle_drag_in_the_thumb_zone_stages_no_move_even_on_a_promotable_card(hub):  # noqa: F811
    out = run_page(hub.MOBILE_PAGE, """
      await loadBoard();
      const card = $("cards").querySelector('[data-role="card"][data-n="7"]');
      const grab = document.createElement("div");
      grab.classList.add("grab");
      grab.dataset.role = "sheet";
      card.appendChild(grab);
      window.innerHeight = 1000;
      __dispatch("touchstart", { target: grab, touches: [{ clientX: 0, clientY: 700 }] });
      __dispatch("touchend", { changedTouches: [{ clientX: 100, clientY: 700 }] });
      return { pending: pending ? pending.n : null };
    """)
    assert out == {"pending": None}, out


def test_the_grab_handle_is_in_flow_so_it_never_overlaps_the_sheet_head(hub):  # noqa: F811
    decls = re.search(r"\.grab\{([^}]*)\}", hub.MOBILE_PAGE).group(1).replace(" ", "")
    assert "position:absolute" not in decls, decls
    assert not re.search(r"margin[^;]*-\d", decls), f"the handle overlaps the head with a negative margin: {decls}"
