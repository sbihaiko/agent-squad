"""Small graph builders and engine runners shared by the engine tests."""
from squad.adapter import ScriptedAdapter
from squad.engine import Engine
from squad.graph import from_doc


def agent(name, **kw):
    n = {"name": name, "kind": "agent", "model": "sonnet", "tools": ["Read"], "budget": {"turns": {"min": 1, "max": 10}, "spend": {"min": 0.01, "max": 1.0}},
         "deadline": 30, "next": {"fail": "repeat"}}
    n.update(kw)
    return n


def doc(nodes, root_children, root_max=1, facts=None, cap=10.0):
    return {"schema_version": 1, "name": "t", "run": {"deadline": 600, "max_parallel": 2, "spend_cap": cap},
            "facts": facts or [{"name": "flag", "type": "bool", "instrument": "code: test flag", "source_kind": "artifact"}],
            "gates": [{"id": "spend-floor", "params": ["min_remaining_usd"]}, {"id": "plan-semantics", "params": ["max_work_items"]}, {"id": "scope", "params": ["allowed_generated"]}],
            "root": "main", "nodes": [{"name": "main", "kind": "loop", "max": {"value": root_max, "min": 1}, "children": root_children}, *nodes]}


def scope_gate(name="gate", fail="repeat"):
    return {"name": name, "kind": "gate", "check": "scope", "params": {"allowed_generated": []}, "next": {"fail": fail}}


def run_engine(d, script, tmp_path, **kw):
    eng = Engine(from_doc(d), ScriptedAdapter(script, **kw.pop("adapter", {})), tmp_path, **kw)
    return eng


BAD = {"output": {"diff": {"files": [{"path": "gen.lock", "generated": True}]}}, "spend": 0.1}
GOOD = {"output": {"diff": {"files": [{"path": "a.py"}]}}, "spend": 0.1}


def actor_gate_loop(max_=2):
    return doc([agent("actor", output_key="diff"), scope_gate(),
                {"name": "work", "kind": "loop", "max": {"value": max_, "min": 1}, "children": ["actor", "gate"]}], ["work"])


