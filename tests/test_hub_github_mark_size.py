"""Every GitHub mark on the board draws at 13 x 13, whatever its container.

The page's global `svg{display:block;width:100%}` overrides the mark's width attribute. In a spec's lane header
the anchor is a flex item that shrinks with the title, so the mark is squashed. These tests read the CSS the
served page carries.
"""
import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib" / "squad-goal" / "scripts" / "hub.py"


def _served_css(tmp_path, monkeypatch):
    monkeypatch.setenv("SG_REPO", "x/y")
    monkeypatch.setenv("SG_OWNER", "x")
    monkeypatch.setenv("SG_PROJECT", "4")
    monkeypatch.setenv("SG_CHECKOUT", str(ROOT))
    monkeypatch.setenv("SG_STATE", str(tmp_path))
    monkeypatch.setenv("SG_HUB_DIR", str(tmp_path))
    spec = importlib.util.spec_from_file_location("hub_github_mark_size", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", mod.PAGE, re.S))


def _bodies(css, selector_pattern):
    return [
        body
        for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css)
        if re.fullmatch(selector_pattern, selector.strip())
    ]


def test_the_mark_keeps_its_own_fixed_box_against_the_global_svg_rule(tmp_path, monkeypatch):
    """A rule scoped to the anchor's svg wins over `svg{width:100%}` and fixes the mark at 13 x 13."""
    css = _served_css(tmp_path, monkeypatch)
    bodies = _bodies(css, r"\.ghl\s*>?\s*svg")
    assert bodies, "the served page sets no size on the GitHub mark inside its anchor"
    assert any("width:13px" in b and "height:13px" in b for b in bodies), bodies


def test_the_mark_anchor_does_not_shrink_in_a_flex_row(tmp_path, monkeypatch):
    """A lane header is a flex row; its GitHub anchor must keep its width, so a long title cannot squash the mark."""
    css = _served_css(tmp_path, monkeypatch)
    bodies = _bodies(css, r"\.ghl")
    assert any("flex:none" in b for b in bodies), bodies
