"""The replay half of `resume`.

`Replay` checks that a record may resume, rebuilds the engine state from it, marks crashed children,
cuts the `--from` subtree, and preflights the workspaces. The engine keeps the dry walk, the `resume`
event, and the conclusion. A reader of pause and resume starts here.
"""
from pathlib import Path

from squad import git, modelreg, skills
from squad.engine_errors import ResumeRefused
from squad.graph import check_node_names


class Replay:
    def __init__(self, engine):
        self.e = engine

    def refuse(self, from_node):
        """Raise `ResumeRefused` for one of the refusal reasons: no run_start, a graph hash change, a model registry change, a skill hash change, a root exit, a bad `--from` name. Return the run id of the record."""
        e = self.e
        ev = e.record.events
        if not ev or ev[0]["event"] != "run_start":
            raise ResumeRefused("the record has no run_start event")
        if ev[0]["graph_hash"] != e.g.hash:
            raise ResumeRefused("the graph hash differs from the record's run_start: edit the graph and start a new run")
        gone = self.model_drift(ev[0])
        if gone:
            raise ResumeRefused(gone)
        gone = self.project_drift(ev[0])
        if gone:
            raise ResumeRefused(gone)
        rec = ev[0].get("state_binding")
        if rec is not None and rec != e.state_binding:
            raise ResumeRefused(f"the issue-state binding changed since the run started (recorded {rec}, now {e.state_binding}): restore it or start a new run")
        rec = ev[0].get("autonomy")      # a record from before ticket #180 has none and is not checked
        if rec and e.autonomy and rec != e.autonomy:
            raise ResumeRefused(f"the autonomy setting changed since the run started (recorded {rec}, now {e.autonomy}): restore it or start a new run")
        have = skills.load(e.vendor)
        drift = self.skill_drift(ev[0], have)
        if drift:
            raise ResumeRefused(f"a skill changed since the run started ({skills.commit_label(ev[0].get('skills_commit'))}): {drift}")
        if any(x["event"] == "outcome" and x["node"] == e.g.root and x.get("item") is None for x in ev):
            raise ResumeRefused("the root has an exit event: the graph ran to its end, start a new run")
        if from_node:
            bad = check_node_names(e.g.doc, [from_node])
            if bad:
                raise ResumeRefused(str(bad[0]))
        return ev[0]["run_id"]

    def model_drift(self, start):
        """A refusal text when the registry entries the graph names changed since the run started, else None.
        A record with no `models.resolve` resumes opaque, and an edit to an entry the graph does not name never refuses."""
        rec = start.get("models") or {}
        if not rec.get("resolve"):
            return None
        e = self.e
        have = modelreg.entries_sha256(e._model_names(), e._registry())
        if rec.get("entries_sha256") != have:
            return (f"the model registry entries the graph names changed since the run started: recorded "
                    f"{rec.get('entries_sha256')}, current {have}: restore the registry or start a new run")
        return None

    def project_drift(self, start):
        """A refusal text when the project settings file changed since the run started, else None. A record with no `project_settings` has none."""
        rec = start.get("project_settings")
        have = (self.e.project.get("record") or {}).get("sha256")
        if rec and have != rec["sha256"]:
            return f"the project settings file {rec['path']} changed since the run started: recorded {rec['sha256']}, current {have or 'gone'}"
        return None

    def skill_drift(self, start, have):
        """One entry per recorded skill whose hash now differs from `have`. A record with no `skills` field has none."""
        return "; ".join(f"{n}: recorded {r['hash']}, current {have.get(n, {}).get('hash', 'gone')}"
                         for n, r in (start.get("skills") or {}).items() if have.get(n, {}).get("hash") != r["hash"])

    def absorb_all(self):
        """Rebuild state from the record: outcomes, artifacts, facts, waits, and spend. Return the missing artifacts."""
        e = self.e
        missing = []
        for ev in e.record.events:
            k = ev["event"]
            if k == "wait":
                e.waits[(ev["node"], ev.get("item"))] = ev
            elif k in ("spawn_accepted", "land"):
                e.dispatch.absorb(ev)
            elif k == "outcome":
                name, item = ev["node"], ev.get("item")
                e.waits.pop((name, item), None)
                if ev["artifact"] and not e.artifacts.load(ev["artifact"], name, item, e.nodes.get(name, {}).get("output_key"), e.run_dir):
                    missing.append(f"{name}:{ev['artifact']}")
                if ev["spend"] is not None:
                    e.budget.measured += ev["spend"]
                elif ev["cap"] is not None:
                    e.budget.unmeasured += ev["cap"]
                e.outcomes.absorb(ev)    # Absorb last. It refreshes the derived facts. Those facts read the artifacts loaded above.
        return missing

    def mark_crashed_children(self):
        """A child with a `call_start` event and no outcome crashed mid-session. It is not run again under its reservation:
        its grant stays reserved as unmeasured spend, never as zero, and the reason `crashed` reaches the verdict."""
        e = self.e
        launched = {x["node"] for x in e.record.events if x["event"] == "call_start"}
        for name in [n for n in e.dispatch.children if n in launched and not e.outcomes.recorded(n)]:
            cap = e.dispatch.children[name]["reservation"]
            e.runs.outcome(name, "fail", ["crashed", "launched_no_outcome"], None, {"cap": cap})
            e.budget.unmeasured += cap

    def cut(self, from_node):
        """The record position of the `--from` cut: what the re-run nodes applied before is not replayed, they propose again."""
        if not from_node:
            return None
        e = self.e
        sub = e.subtree(from_node)
        return min((x["pos"] for x in e.record.events if (x["event"] == "outcome" and x["node"] in sub)
                    or (x["event"] == "proposed" and x["proposer"] in sub)), default=None)

    def preflight_workspaces(self, frontier):
        """Each workspace that a node about to run uses: present, on its recorded HEAD, in its recorded state."""
        e = self.e
        if not e.has_git and any(e.nodes[n].get("workspace") not in (None, ".") for n, _ in frontier if n in e.nodes):
            return {"status": "failed", "reason": "git_missing"}
        ran = {x["node"] for x in e.record.events if x["event"] == "outcome"}
        needed = {e.nodes[n].get("workspace") for n, _ in frontier if n in e.nodes}
        needed |= {e.dispatch.children[n]["definition"]["workspace"] for n, _ in frontier if n in e.dispatch.children}
        needed |= {n.get("workspace") for k, n in e.nodes.items() if k not in ran}
        needed -= {None, "."}
        last = {}
        for x in e.record.events:
            w = x.get("workspace") if x["event"] == "outcome" else None
            if w and w["name"] in needed:
                last[(w["name"], x.get("item"), w.get("child"))] = w
        for (logical, item, child), rec in last.items():
            p = e.run_dir / "ws" / Path(rec["path"]).name if child else e.workspace_path(logical, item)
            if not p.exists():
                return {"status": "failed", "reason": f"worktree_missing:{logical}"}
            if git.head(p) != rec["head"]:
                return {"status": "conflict", "reason": f"workspace_head_moved:{logical}"}
            if git.state(p) != rec["state"]:
                return {"status": "conflict", "reason": f"workspace_changed:{logical}"}
        return {"status": "ok"}

    def resume_fields(self, frontier, pre, answer, from_node):
        """The fields of the `resume` event."""
        e = self.e
        labels = [n if i is None else f"{n}#{i}" for n, i in frontier]
        opts = {k: v for k, v in {**e.options, "answer": answer, "from": from_node}.items() if v is not None}
        late = {} if answer else {"default_applied": [n for (n, i), w in e.waits.items() if e.wall() >= w["deadline_at"]]}
        late = {k: v for k, v in late.items() if v}
        recorded = e.record.events[0].get("skills")
        have = skills.load(e.vendor)
        return {"run_id": e.run_id, "graph_hash": e.g.hash, "frontier": labels,
                **({"skills": {n: have[n] for n in recorded if n in have}} if recorded else {}), "invoker": e.invoker,
                "engine_options": opts, "preflight": pre, **late, **({"defaults": e.defaults} if e.defaults else {})}
