"""Hub footer: when token use is high, the line says what the window cost and how many calls crossed the threshold.

`tokens.py burners --json` decides: over or not, the threshold, the order. The hub only draws what it prints.
The consumers the script names are machine ids (`run:<id>:<node>`) and the count of the ones that did not fit was a third
number to hold while reading them, so the owner read the block as soup (2026-10-16): they are the block's tooltip now.
A missing script or a nonzero exit is no data: nothing is drawn and nothing raises.
"""
import importlib.util
import json
import os
import tempfile
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ.setdefault("SG_CHECKOUT", tempfile.mkdtemp())
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_token_burn", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def stub(tmp_path, payload, rc=0):
    """A fake tokens.py that prints `payload` and exits `rc`."""
    p = tmp_path / "tokens.py"
    p.write_text(textwrap.dedent(f"""\
        import sys
        print({json.dumps(json.dumps(payload))})
        sys.exit({rc})
        """))
    return p


def burners(*rows):
    return [{"consumer": c, "tokens": t, "calls": 1, "models": ["m"]} for c, t in rows]


def facts(hub, burn):
    return hub.foot_items({"burn": burn}, 0.0)


def burn_text(hub, burn):
    """The burn block's own text as the line receives it: the line carries the other facts beside it."""
    return [i for i in facts(hub, burn) if i["key"] == "burn"][0]["text"]


def test_over_states_the_window_and_the_count(hub, tmp_path):
    s = stub(tmp_path, {"window_hours": 24, "threshold": 2000000, "total": 3000000, "over": True, "brief": "x",
                        "burners": burners(("review:PR#432", 1500000), ("dispatch:claim-9", 900000))})
    burn = hub.burn_read(script=s, state=tmp_path, checkout=tmp_path)
    text = burn_text(hub, burn)
    assert text == "tokens 2.4M/24h · 2 over 2M"                     # what the window cost, and how many crossed
    assert "PR#432" not in text and "dispatch" not in text           # no machine id on the line, ever
    tip = hub._burn_tip(burn)
    assert "2 calls, each over 2M in 24h" in tip                      # the sentence the two numbers stand for
    assert "review:PR#432 1.5M" in tip and "dispatch:claim-9 900k" in tip   # and the names, in the script's own order


def test_the_burn_block_states_two_numbers_and_never_the_names(hub):
    """The block is capped: the footer drops whole blocks when the line is too wide, so a block that never ends would
    empty the line it is the first fact of (#446). Two numbers cannot outgrow it; the names are the hover's."""
    rows = [(f"review:PR#{n}", 1000000 - n) for n in range(8)]
    burn = {"over": True, "threshold": 1, "burners": burners(*rows), "window_hours": 24}
    text = burn_text(hub, burn)
    assert text == "tokens 8M/24h · 8 over 1", text
    assert "PR#" not in text                                          # a name is not a token of the line any more
    assert len(hub._burn_fact(burn)) <= hub.BURN_MAX_CHARS, hub._burn_fact(burn)
    tip = hub._burn_tip(burn)
    assert [x for x in ("PR#0", "PR#1", "PR#2", "PR#3") if x in tip] == ["PR#0", "PR#1", "PR#2"]   # the worst, in order
    assert tip.endswith("+5 more"), tip                               # and the rest as one count


def test_a_reading_the_hub_cannot_compare_still_states_what_it_has(hub):
    """No threshold read: the block names the cap as the one word it can, and states the same two numbers."""
    burn = {"over": True, "burners": burners(("review:PR#1", 1500000)), "window_hours": 24}
    assert hub._burn_fact(burn) == "tokens 1.5M/24h · 1 over the cap"
    assert hub._burn_tip(burn) == "1 calls, each over the cap in 24h · review:PR#1 1.5M"
    assert hub._burn_fact({"over": True, "threshold": 1, "burners": []}) == ""   # no rows, no block


def test_under_threshold_renders_nothing(hub, tmp_path):
    s = stub(tmp_path, {"window_hours": 24, "threshold": 2000000, "total": 10, "over": False, "brief": "",
                        "burners": burners(("review:PR#1", 10))})
    burn = hub.burn_read(script=s, state=tmp_path, checkout=tmp_path)
    assert hub.foot_items({"burn": burn}, 0.0) == hub.foot_items({}, 0.0)


def test_missing_script_renders_nothing_and_does_not_raise(hub, tmp_path):
    burn = hub.burn_read(script=tmp_path / "absent.py", state=tmp_path, checkout=tmp_path)
    assert burn is None
    assert hub.foot_items({"burn": burn}, 0.0) == hub.foot_items({}, 0.0)


def test_nonzero_exit_and_garbage_are_no_data(hub, tmp_path):
    assert hub.burn_read(script=stub(tmp_path, {"over": True}, rc=2), state=tmp_path, checkout=tmp_path) is None
    g = tmp_path / "g.py"
    g.write_text("print('not json')\n")
    assert hub.burn_read(script=g, state=tmp_path, checkout=tmp_path) is None


def test_the_view_holds_no_rule(hub):
    # `over` decides, not the totals: huge burners with over false draw nothing; tiny ones with over true draw.
    quiet = {"over": False, "threshold": 5, "burners": burners(("review:PR#1", 99999999))}
    loud = {"over": True, "threshold": 5, "burners": burners(("review:PR#2", 1))}
    assert facts(hub, quiet) == facts(hub, None)
    assert burn_text(hub, loud) == "tokens 1/24h · 1 over 5"
    # the order is the script's: the hub does not re-sort
    mixed = {"over": True, "threshold": 5, "burners": burners(("a:first", 1), ("b:second", 999))}
    t = hub._burn_tip(mixed)
    assert t.index("a:first") < t.index("b:second")
