"""The hub pages name their language, their landmarks and every control, and no control is under 28 px tall (#912).

The desktop page and the phone page are read as served strings. A control is named by aria-label, aria-labelledby,
an enclosing label or a label's `for`; a placeholder alone is not a name. The height check reads the declared px
heights of every rule whose selector names a control. Controls built by script at run time are not seen here.
"""
import importlib.util
import os
import re
import tempfile
from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"
MIN_CONTROL_PX = 28
CONTROL = re.compile(r"(^|[\s>+~,])(button|select|input|textarea|summary)\b|\[role=button\]")
HEIGHT = re.compile(r"(?<![\w-])(?:min-)?height\s*:\s*(\d+(?:\.\d+)?)px")


class _Landmarks(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.html_lang = None
        self.landmarks = set()

    def handle_starttag(self, tag, attrs):
        if tag == "html":
            self.html_lang = dict(attrs).get("lang")
        if tag in ("main", "aside"):
            self.landmarks.add(tag)


CONTROL_TAG = re.compile(r"<(input|select|textarea)\b((?:[^>\"']|\"[^\"]*\"|'[^']*')*)>")
LABEL_FOR = re.compile(r"<label\b[^>]*?\bfor=[\"']?([\w-]+)")


def _markup(page):
    p = _Landmarks()
    p.feed(page)
    p.close()
    return p


def _inside_label(page, pos):
    before = page[:pos]
    return before.rfind("<label") > before.rfind("</label>")


def _unnamed(page):
    fors = set(LABEL_FOR.findall(page))
    out = []
    for m in CONTROL_TAG.finditer(page):
        attrs = m.group(2)
        if re.search(r"(?<![\w-])type=[\"']?(hidden|button|submit|reset)\b", attrs):
            continue
        ident = re.search(r"(?<![\w-])id=[\"']?([\w-]+)", attrs)
        named = (
            re.search(r"(?<![\w-])aria-(label|labelledby)=", attrs)
            or (ident and ident.group(1) in fors)
            or _inside_label(page, m.start())
        )
        if not named:
            out.append(m.group(0)[:120])
    return out


def _short_controls(page):
    css = "".join(re.findall(r"<style>(.*?)</style>", page, flags=re.S))
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    out = []
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        if not any(CONTROL.search(s) for s in m.group(1).split(",")):
            continue
        for h in HEIGHT.findall(m.group(2)):
            if float(h) < MIN_CONTROL_PX:
                out.append(f"{m.group(1).strip()} -> {h}px")
    return out


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ.setdefault("SG_CHECKOUT", tempfile.mkdtemp())
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_accessibility", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(params=["PAGE", "MOBILE_PAGE"])
def served(request, hub):
    return request.param, getattr(hub, request.param)


def test_every_hub_page_declares_its_language(served):
    name, page = served
    assert _markup(page).html_lang == "en", f"{name} has no lang=en on its html element"


def test_the_desktop_page_has_main_and_aside_landmarks(hub):
    assert {"main", "aside"} <= _markup(hub.PAGE).landmarks


def test_the_phone_page_has_a_main_landmark(hub):
    assert "main" in _markup(hub.MOBILE_PAGE).landmarks


def test_every_form_control_has_a_name(served):
    name, page = served
    assert _unnamed(page) == [], f"{name} has unnamed controls"


def test_no_control_is_declared_under_28_px_tall(served):
    name, page = served
    assert _short_controls(page) == [], f"{name} declares a control under {MIN_CONTROL_PX} px"
