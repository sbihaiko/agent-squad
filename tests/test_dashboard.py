"""Guards for the run dashboard build: the built file is current, one source, one bundle, and the origin rule.

The other dashboard tests live in tests/test_dashboard_<concern>.py; shared helpers in tests/dashboard_helpers.py.
"""
import hashlib
import json
import re
import sys

from squad import graph, modelreg
from tests.dashboard_helpers import ROOT, run_node

sys.path.insert(0, str(ROOT / "scripts"))
from build_dashboard import CORE_DIR, CORE_ORDER, OUT, PAGE_CSS, PAGE_DIR, PAGE_HTML, PAGE_ORDER, build, core_bundle, engine_data, has_price, page_bundle, page_sources, served_lists  # noqa: E402


# --- one source: the built file is the template plus the engine's own schema, registry, and lists ---
def test_the_built_dashboard_is_current():
    assert OUT.read_text() == build(), "the built dashboard is stale: run python3 scripts/build_dashboard.py"


def test_the_dashboard_embeds_the_engine_schema_registry_and_served_lists_not_a_copy():
    text = OUT.read_text()
    data = engine_data()
    assert json.dumps(data["schema"], sort_keys=True).replace("</", "<\\/") in text
    assert json.dumps(data["gate_registry"], sort_keys=True) in text
    assert data["schema"] == json.loads(graph.SCHEMA_PATH.read_text())
    served = served_lists()
    assert json.dumps(served, sort_keys=True) in text and len(served["hash"]) == 64
    assert {"Read", "claude-deepseek-v4-flash", "medium", "tdd"} <= set(served["tools"] + served["models"] + served["efforts"] + served["skills"])


def test_a_display_price_with_no_number_is_not_shown():
    """#798: an entry whose display price has no input and no output number (gpt-6-luna, all null) is not a price."""
    prices = engine_data()["models"]["prices"]
    assert all(isinstance(p.get("input"), (int, float)) or isinstance(p.get("output"), (int, float)) for p in prices.values()), prices
    reg = modelreg.default_registry()
    assert all(mid not in prices for mid, e in reg.entries.items() if not has_price(e.display_price)), "an entry with no display number is shown"


def test_has_price_needs_a_number_for_input_or_output():
    """#799: has_price is true only for a number (not a bool) in input or output."""
    assert not has_price({"input": None, "output": None})
    assert has_price({"input": 1.0, "output": None})
    assert has_price({"input": None, "output": 2.5})
    assert not has_price({"input": True, "output": False})
    assert not has_price({})
    assert not has_price(None)


def test_the_dashboard_embeds_the_registry_snapshot_and_the_registry_models():
    """Labels, aliases and display prices come from the one registry, and the served model list is its ids and aliases."""
    reg = modelreg.default_registry()
    data = engine_data()["models"]
    assert data["labels"] == {mid: e.label for mid, e in reg.entries.items()}
    assert data["aliases"] == dict(reg.aliases)
    assert all(modelreg.model(sp, reg).id == mid for sp, mid in data["spellings"].items())   # a served spelling reads as its entry (#677)
    assert data["prices"] == {mid: e.display_price for mid, e in reg.entries.items() if has_price(e.display_price)}
    assert json.dumps(data, sort_keys=True).replace("</", "<\\/") in OUT.read_text()
    assert set(served_lists()["models"]) == set(reg.entries) | set(reg.aliases)


def test_every_core_module_is_bundled_once_and_the_runner_and_page_share_the_bundle():
    """#120: dashboard/core/ holds one file per concern; the build bundles each exactly once. The page and the node runner get one text."""
    files = sorted(p.name for p in CORE_DIR.glob("*.js"))
    assert sorted(CORE_ORDER) == files and len(set(CORE_ORDER)) == len(CORE_ORDER), "CORE_ORDER and dashboard/core/ differ"
    bundle = core_bundle()
    for f in files:
        assert bundle.count((CORE_DIR / f).read_text().replace("</script", "<\\/script")) == 1, f
    page = OUT.read_text()
    assert page.count(bundle) == 1 and "/*CORE*/" not in page
    assert run_node([{"op": "hash", "text": "{}"}])[0] == hashlib.sha256(b"{}").hexdigest()   # the runner evaluates this bundle


def test_every_page_source_is_built_once_and_the_page_bundle_is_the_one_script():
    """#119: dashboard/page/ holds one script file per concern. The build bundles each exactly once; a file left out of PAGE_ORDER or listed twice fails here."""
    files = sorted(p.name for p in PAGE_DIR.glob("*.js"))
    assert sorted(PAGE_ORDER) == files and len(set(PAGE_ORDER)) == len(PAGE_ORDER), "PAGE_ORDER and dashboard/page/ differ"
    bundle = page_bundle()
    for f in files:
        assert bundle.count((PAGE_DIR / f).read_text().replace("</script", "<\\/script")) == 1, f
    page = OUT.read_text()
    assert page.count(bundle) == 1 and page.count(PAGE_CSS.read_text()) == 1
    assert [p.name for p in page_sources()] == ["page.html", "page.css", *PAGE_ORDER]
    assert PAGE_HTML.read_text().count("<script") == 5 and not (ROOT / "dashboard" / "dashboard.template.html").exists()


def test_the_dashboard_reaches_only_its_own_origin_and_only_the_run_paths():
    """A page from file:// makes no request. A page that `squad dashboard` serves fetches
    only relative paths of its own origin: the run info, the record and the graph. No absolute URL, no other transport."""
    text = "".join(p.read_text() for p in [PAGE_HTML, PAGE_CSS, *sorted(PAGE_DIR.glob("*.js")), *sorted(CORE_DIR.glob("*.js"))])  # every source file on disk, so none escapes  # code only; the schema's $id is an identifier
    text = text.replace("http://www.w3.org/2000/svg", "")  # the SVG namespace is an identifier, not an address to fetch
    assert OUT.read_text().count("<script") == 5
    assert not re.search(r"/\*(ENGINE|EXAMPLE|SERVED|CORE|CSS|PAGE)\*/", OUT.read_text()), "a placeholder is left in the built page"
    for word in ("http://", "https://", "XMLHttpRequest", "WebSocket", "sendBeacon", "import(", " src="):
        assert word not in text, word
    assert re.findall(r"new EventSource\(([^)]*)\)", text) == ["path"]   # one push stream, and its path is the one the served source names
    assert re.findall(r'streamFn\("(run/[a-z.]+)"', text) == ["run/events"]
    calls = re.findall(r"fetch\(([^,)]+)", text)
    assert sorted(set(calls)) == ['"run/control"', '"run/control.json"', '"run/info.json"', "p"], calls   # the info call, the control state and its one write, and the one that the served source wraps
    assert sorted(re.findall(r'get\("(run/[a-z.]+)"\)', text)) == ["run/graph.json", "run/record.jsonl"]


