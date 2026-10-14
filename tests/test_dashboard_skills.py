"""#143: the Harness panel lists each run's skill hashes (short; full on hover) and the vendor commit. An old record reads "unrecorded"."""
import json

import pytest

from tests.dashboard_helpers import _run22, run_node
from tests.test_dashboard_page import drive, needs_ego

HASH = "ab12cd34" + "e" * 56


def _with_skills():
    doc, events = _run22()
    new = json.loads(json.dumps(events))
    new[0].update({"skills": {"tdd": {"hash": HASH, "source": "vendor"}}, "skills_commit": "134516bcafe0"})
    return doc, events, new


def test_the_engine_harness_carries_the_skill_hashes_and_the_vendor_commit_and_an_old_record_has_none():
    doc, old, new = _with_skills()
    assert run_node([{"op": "stats", "events": old, "doc": doc, "shown": True}])[0]["engine"]["skills"] is None
    e = run_node([{"op": "stats", "events": new, "doc": doc, "shown": True}])[0]["engine"]
    assert e["skills"] == [{"name": "tdd", "hash": HASH, "source": "vendor"}] and e["skills_commit"] == "134516bcafe0"


def _panel(tmp_path, events, doc):
    from tests.dashboard_helpers import RUN22
    rtext = "\n".join(json.dumps(e) for e in events) + "\n"
    steps = (f'put("f-graph",{json.dumps((RUN22 / "graph.json").read_text())},"graph.json");await wait();put("f-record",{json.dumps(rtext)},"record.jsonl");'
             'const spin=()=>new Promise(r=>{const c=new MessageChannel();c.port1.onmessage=()=>r();c.port2.postMessage(0);});'
             'for(let i=0;i<200000&&!document.getElementById("side").textContent.includes("Harness");i++)await spin();await wait();'
             'document.title="RESULT "+JSON.stringify({text:document.getElementById("side").textContent,titles:[...document.querySelectorAll("#side [title]")].map(x=>x.title)});')
    return drive(tmp_path, steps)


@needs_ego
@pytest.mark.heavy
def test_the_page_shows_short_hashes_with_the_full_hash_on_hover_and_unrecorded_for_an_old_record(tmp_path):
    doc, old, new = _with_skills()
    got = _panel(tmp_path, new, doc)
    assert "tdd ab12cd34" in got["text"] and HASH not in got["text"] and "vendor commit 134516bc" in got["text"]
    assert f"tdd {HASH}" in got["titles"] and "134516bcafe0" in got["titles"]
    assert "Skills: unrecorded · vendor commit unrecorded" in _panel(tmp_path, old, doc)["text"]
