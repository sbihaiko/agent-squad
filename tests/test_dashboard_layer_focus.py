"""The help overlay is a layer: it takes focus, the page behind it goes inert, closing returns focus to its opener, and Escape closes only the top layer."""
import pytest

from tests.test_dashboard_page import drive, needs_ego

OPEN_HELP = 'const opener=document.getElementById("b-help");opener.focus();opener.click();await wait();'
OPEN_SIDE = 'document.getElementById("b-panel").click();await wait();'
ESCAPE = 'document.activeElement.dispatchEvent(new KeyboardEvent("keydown",{key:"Escape",bubbles:true,cancelable:true}));await wait();'


@needs_ego
@pytest.mark.heavy
def test_opening_a_layer_moves_focus_into_it(tmp_path):
    got = drive(tmp_path, OPEN_HELP +
                'const help=document.getElementById("help");'
                'document.title="RESULT "+JSON.stringify({open:!help.hidden,focusIn:help.contains(document.activeElement)});')
    assert got == {"open": True, "focusIn": True}


@needs_ego
@pytest.mark.heavy
def test_the_page_behind_an_open_layer_is_inert(tmp_path):
    got = drive(tmp_path, OPEN_HELP +
                'const panel=document.getElementById("b-panel");panel.focus();const behind=document.activeElement===panel;'
                'const close=document.getElementById("b-help-close");close.focus();const inside=document.activeElement===close;'
                'document.title="RESULT "+JSON.stringify({behind,inside});')
    assert got == {"behind": False, "inside": True}


@needs_ego
@pytest.mark.heavy
def test_closing_a_layer_returns_focus_to_the_control_that_opened_it(tmp_path):
    got = drive(tmp_path, OPEN_HELP +
                'const close=document.getElementById("b-help-close");close.focus();close.click();await wait();'
                'const back=document.activeElement&&document.activeElement.id;'
                'const panel=document.getElementById("b-panel");panel.focus();const pageLive=document.activeElement===panel;'
                'document.title="RESULT "+JSON.stringify({hidden:document.getElementById("help").hidden,back,pageLive});')
    assert got == {"hidden": True, "back": "b-help", "pageLive": True}


@needs_ego
@pytest.mark.heavy
def test_escape_closes_the_top_layer_once_and_the_page_handler_does_not_run(tmp_path):
    got = drive(tmp_path, OPEN_SIDE + OPEN_HELP +
                'const sideBefore=!document.body.classList.contains("noside");' + ESCAPE +
                'const afterOne={helpHidden:document.getElementById("help").hidden,sideOpen:!document.body.classList.contains("noside")};' + ESCAPE +
                'const afterTwo={sideOpen:!document.body.classList.contains("noside")};'
                'document.title="RESULT "+JSON.stringify({sideBefore,afterOne,afterTwo});')
    assert got["sideBefore"] is True
    assert got["afterOne"] == {"helpHidden": True, "sideOpen": True}
    assert got["afterTwo"] == {"sideOpen": False}
