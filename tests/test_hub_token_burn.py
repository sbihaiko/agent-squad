"""Hub footer: when token use is high, the line names who burns the tokens (the owner's goal).

`tokens.py burners --json` decides: over or not, the threshold, the order. The hub only draws what it prints.
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


def test_over_names_consumers_and_tokens(hub, tmp_path):
    s = stub(tmp_path, {"window_hours": 24, "threshold": 2000000, "total": 3000000, "over": True, "brief": "x",
                        "burners": burners(("review:PR#432", 1500000), ("dispatch:claim-9", 900000))})
    burn = hub.burn_read(script=s, state=tmp_path, checkout=tmp_path)
    text = hub.foot_line(facts(hub, burn))
    assert "review:PR#432" in text and "1.5M" in text
    assert "dispatch:claim-9" in text and "900k" in text
    assert "2M" in text   # the threshold is named, so the owner can raise it


def test_the_burn_block_names_what_fits_and_counts_the_rest(hub):
    """The block is capped: the footer drops whole blocks when the line is too wide, so a block that never ends would
    empty the line it is the first fact of (#446). Names fit to BURN_MAX_CHARS, the rest is one count."""
    rows = [(f"review:PR#{n}", 1000000 - n) for n in range(8)]
    burn = {"over": True, "threshold": 1, "burners": burners(*rows), "window_hours": 24}
    text = hub.foot_line(facts(hub, burn))
    assert "PR#0" in text and "PR#1" in text, text                   # the worst burners, in the script's own order
    assert "PR#2" not in text and "+6" in text, text                 # the other six are one count, never five more names
    assert len(hub._burn_fact(burn)) <= hub.BURN_MAX_CHARS, hub._burn_fact(burn)


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
    assert "review:PR#2" in hub.foot_line(facts(hub, loud))
    # the order is the script's: the hub does not re-sort
    mixed = {"over": True, "threshold": 5, "burners": burners(("a:first", 1), ("b:second", 999))}
    t = hub.foot_line(facts(hub, mixed))
    assert t.index("a:first") < t.index("b:second")
