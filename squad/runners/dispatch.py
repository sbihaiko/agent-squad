"""Dispatch decisions and landing.

`Dispatch` owns the spawned children, their held reservations, the landed children, and the held-out
baseline. It decides spawn requests ("decide these requests") and lands finished children ("land these
children"). It reads and writes the spawn, land, and ledger events. The engine runs the children's sessions.
"""
import subprocess

import anyio

from squad import actions, envelope, git, session, workcmd
from squad import verdict as V
from squad.engine_errors import Frontier


class Dispatch:
    def __init__(self, engine):
        self.e = engine
        self.children, self.held = {}, {}   # spawned children by name (their spawn_accepted event), and their held reservations
        self.landed = {}       # dispatch child name -> its `land` event
        self.ac_before = None  # {child: its held-out commands all passed}, as of the last landing

    def absorb(self, ev):
        """Rebuild state from one record event on resume."""
        if ev["event"] == "spawn_accepted":
            self.children[ev["child"]] = ev
        elif ev["event"] == "land":
            self.landed[ev["node"]] = ev

    def payload(self, name):
        """The dispatch artifact: every held-out command of every child that passed, in all waves (a later wave re-runs the earlier ones)."""
        ledger = self.ledger(name)
        cmds = []
        for row in ledger:
            out = self.e.artifacts.get(row["child"], None)
            for c in session.ac_commands(out)[0] if row["outcome"] == "pass" else []:
                if c not in cmds:
                    cmds.append(c)
        return ledger, cmds

    def spawn_events(self, parent, pentry):
        return [e for e in self.e.record.events if e["event"] == "spawn_requested" and e["parent"] == parent and e.get("parent_entry") == pentry]

    def decision(self, key):
        return next((e for e in self.e.record.events if e["event"] in ("spawn_accepted", "spawn_rejected") and e["request_key"] == key), None)

    def rejections(self, parent, pentry):
        """Why each request of this entry was rejected, in request order, without repeats: the reasons a `no_dispatch` verdict hides."""
        out = []
        for req in self.spawn_events(parent, pentry):
            dec = self.decision(req["pos"])
            if dec and dec["event"] == "spawn_rejected":
                out += [f"spawn_rejected:{r}" for r in dec.get("reasons") or [dec["reason"]] if f"spawn_rejected:{r}" not in out]
        return out

    def accepted(self, parent, pentry):
        return [e["child"] for e in self.e.record.events if e["event"] == "spawn_accepted" and e["parent"] == parent and e["parent_entry"] == pentry]

    def decide(self, disp, req_ev, pentry, depth, parent_tools, spend_left, time_left):
        """Validate one request against the envelope. Write `spawn_accepted` or `spawn_rejected`. Code assigns the identity."""
        key, parent, raw = req_ev["pos"], req_ev["parent"], req_ev["requested"]
        base = dict(request_key=key, parent=parent, envelope=disp["name"])
        if not isinstance(raw, dict):
            return self.e.record.append("spawn_rejected", **base, reason="request_malformed")
        if len(self.accepted(parent, pentry)) >= disp["max_children"]:
            return self.e.record.append("spawn_rejected", **base, reason="max_children_over")
        req, errs = envelope.validate_request(disp, raw, depth, parent_tools, min(spend_left, self.e.budget.available()), time_left, self.e.vendor)
        if not errs:
            errs += self.resolve_after(req, parent, pentry, req_ev)
        res = None if errs else self.e.budget.hold(req["spend"])
        if not errs and res is None:
            errs = ["spend_over_remainder"]
        if errs:
            return self.e.record.append("spawn_rejected", **base, reason=errs[0], reasons=errs)
        n = 1 + sum(1 for e in self.e.record.events if e["event"] == "spawn_accepted" and e["envelope"] == disp["name"])
        child = f"{disp['name']}.{disp['entry']}.{n}"
        ev = self.e.record.append("spawn_accepted", request_key=key, child=child, parent=parent, depth=depth, envelope=disp["name"],
                                reservation=req["spend"], parent_entry=pentry, definition=req)
        self.children[child] = ev
        self.held[child] = res
        return ev

    def resolve_after(self, req, parent, pentry, req_ev):
        """Turn `after` indexes into child names. Each index names an earlier request of the same list that was accepted."""
        prior = self.spawn_events(parent, pentry)
        me = next(i for i, e in enumerate(prior) if e["pos"] == req_ev["pos"])
        names, errs = [], []
        for j in req["after"]:
            d = self.decision(prior[j]["pos"]) if j < me else None
            if d is None or d["event"] != "spawn_accepted":
                errs.append(f"after_unmet:{j}")
            else:
                names.append(d["child"])
        req["after"] = names
        return errs

    def lift(self, disp, parent, pentry, depth, spawns, parent_tools, spend_left, time_left, session):
        """Write a request event for each spawn in the list, then its decision. A request with no decision is decided now."""
        prior = self.spawn_events(parent, pentry)
        for i, raw in enumerate(spawns if isinstance(spawns, list) else []):
            req = prior[i] if i < len(prior) else self.e.record.append(
                "spawn_requested", parent=parent, parent_entry=pentry, depth=depth, proposer_session=session, requested=raw)
            if self.decision(req["pos"]) is None:
                self.decide(disp, req, pentry, depth, parent_tools, spend_left, time_left)
        src = self.e._src()
        self.e.facts.produce("depth", depth, src)
        self.e.facts.produce("children_count", len(self.accepted(parent, pentry)), src)
        self.e.facts.produce("spawn_rejected_count", sum(1 for e in self.e.record.events if e["event"] == "spawn_rejected"), src)

    def lift_pending(self, parent, pentry, spawns):
        """True when lifting would still write events: a spawn with no request event, or a request with no decision."""
        prior = self.spawn_events(parent, pentry)
        n = len(spawns) if isinstance(spawns, list) else 0
        return n > len(prior) or any(self.decision(e["pos"]) is None for e in prior)

    def landable(self, name):
        d = self.children[name]["definition"]
        return self.e.has_git and self.e.runs.isolated(d) and (name, 1, None) in self.e.done and self.e.done[(name, 1, None)]["outcome"] == "pass"

    def landed_outcome(self, name, outcome):
        return "fail" if name in self.landed and self.landed[name]["outcome"] != "pass" else outcome

    def landed_reasons(self, name, reasons):
        return list(self.landed[name]["reasons"]) if name in self.landed and self.landed[name]["outcome"] != "pass" else reasons

    async def land(self, node, entry, sc, seen):
        """Land every passing write child's branch into the target, in request order, once.
        A landing that turns an earlier child's held-out command red is undone.
        Code merges: no diff, conflict text or test output reaches a model. Return True in a dry walk when a landing is still owed."""
        for name, outcome, _ in seen:
            if outcome != "pass" or name in self.landed or not self.landable(name):
                continue
            if self.e.dry:
                return True
            d = self.children[name]["definition"]
            path = self.e.runs.workspace(name, d)
            branch = actions.branch_name(self.e.run_id, path.name, None)
            if self.ac_before is None:      # the first landing of this walk or resume: the target as it stands is the baseline
                self.ac_before, _ = await self.ac_survey()
            head_before = git.head(self.e.target)
            if not path.exists():      # a passed writer whose worktree is gone: its work cannot land, and the record says so
                r = actions.ActionResult(False, ["land_workspace_missing"], head_before)
            else:
                try:
                    r = await anyio.to_thread.run_sync(actions.merge_land, self.e.target, path, branch, f"squad: land {name} ({self.e.run_id})", 300 * self.e.scale)
                except subprocess.TimeoutExpired:
                    r = actions.ActionResult(False, ["land_timeout"], head_before)
            if r.ok:
                r = await self.landing_check(name, head_before)
                if r.ok and name in self.ac_before:      # the child's own command, run at its own landing: no earlier result, so it never undoes the merge
                    r.extra["held_out"] = "pass" if self.ac_before[name] else "fail"
            self.landed[name] = self.e.record.append("land", node=name, parent=node["name"], entry=entry,
                                                   outcome="pass" if r.ok else "fail", reasons=list(r.reasons),
                                                   effect={"name": "merge_land", "head": r.head, **r.extra})
        return False

    def prune(self):
        """Remove the branch and worktree of every child that landed. The work is in the target's history now."""
        for name, land in self.landed.items():
            if land["outcome"] != "pass" or name not in self.children:
                continue
            path = self.e.runs.workspace(name, self.children[name]["definition"])
            if path.exists():
                git.run(["worktree", "remove", "--force", str(path)], self.e.target)
            git.run(["branch", "-D", actions.branch_name(self.e.run_id, path.name, None)], self.e.target)

    def landed_commands(self, extra=None):
        """One row per landed child that declares held-out commands: (child, commands), in the order they landed."""
        names = [*self.landed, *([extra] if extra and extra not in self.landed else [])]
        rows = []
        for n in names:
            out = self.e.artifacts.get(n, None)
            cmds = session.ac_commands(out)[0]
            if cmds:
                rows.append((n, cmds))
        return rows

    async def ac_survey(self, extra=None):
        """Run the held-out commands of every landed child against the target. Use `workcmd`, the policy of the gate.
        Return ({child: all its commands passed}, {child: {"ac_failed:<command>": output tail or timeout note}}).
        The evidence holds every failing command of a child."""
        got, evidence = {}, {}
        for name, cmds in self.landed_commands(extra):
            got[name] = True
            for c in cmds:
                r = await anyio.to_thread.run_sync(lambda c=c: workcmd.run(c, self.e.target))
                if not r.ok:
                    got[name] = False
                    evidence.setdefault(name, {})[f"{'malformed' if r.malformed else 'ac_failed'}:{c}"] = r.evidence
        return got, evidence

    async def landing_check(self, name, head_before):
        """A command that passed before this landing and fails after it is a regression: undo the merge.
        A command already failing before it fails nothing: this rule asks only what the landing moved, and
        The rules leave the final gate as the check on a command that was already red. A child's own
        command at its own landing has no earlier result to move from, so the final gate stays the check on it."""
        after, evidence = await self.ac_survey(extra=name)
        moved = sorted(c for c, ok in self.ac_before.items() if ok and not after.get(c, False))
        if not moved:
            self.ac_before = after
            return actions.ActionResult(True, [], git.head(self.e.target))
        await anyio.to_thread.run_sync(lambda: git.run(["reset", "--hard", head_before], self.e.target, timeout=300 * self.e.scale))
        return actions.ActionResult(False, [f"ac_regression:{c}" for c in moved], head_before,
                                    {"evidence": {f"ac_regression:{c}": evidence.get(c, {}) for c in moved}})

    def ledger(self, disp):
        """One compact row per finished child of a dispatch, in request order: the only thing a router reads of its children's work."""
        rows = []
        for acc in [e for e in self.e.record.events if e["event"] == "spawn_accepted" and e["envelope"] == disp]:
            name = acc["child"]
            out = self.e.done.get((name, 1, None))
            if out is None:
                continue
            art = self.e.artifacts.get(name, None)
            files = [f.get("path") for f in ((art or {}).get("diff") or {}).get("files", []) if isinstance(f, dict)] if isinstance(art, dict) else []
            land = self.landed.get(name)
            outcome = self.landed_outcome(name, out["outcome"])
            rows.append({"child": name, "task": acc["definition"]["prompt"][:160], "outcome": outcome,
                         "reasons": [str(r)[:120] for r in self.landed_reasons(name, out["reasons"])][:3], "files": files[:20],
                         "checks": out.get("checks") or {}, "landed_head": land["effect"]["head"] if land and land["outcome"] == "pass" else None,
                         "held_out": land["effect"].get("held_out") if land else None})
        return rows


class DispatchRunner:
    async def run(self, ctx, node, entry, sc):
        d, t0, ent = ctx.dispatch, ctx.clock(), node["entry"]   # wall time of this entry, from reading its entry child to landing
        res = await ctx.exec_node(ent, sc)
        if res.outcome != "pass":
            return ctx.finish(node, entry, sc, res.outcome, res.reasons, elapsed=ctx.clock() - t0)
        pe, spawns = ctx.entry_count(ent, sc.item), ctx.artifacts.get(ctx.nodes[ent]["output_key"], sc.item)
        if ctx.dry and d.lift_pending(ent, pe, spawns):
            raise Frontier([(node["name"], sc.item)])
        stop = isinstance(spawns, list) and not spawns and (ctx.artifacts.get(ent, sc.item) or {}).get("done") is True
        diag = ctx.outcome_event(ent, pe, sc.item).get("diagnostic") or {}
        d.lift(node, ent, pe, 1, spawns, None, ctx.budget.available(), None, diag.get("session", "unknown"))   # the envelope's tool ceiling bounds a top-level child
        seen = await ctx.runs.run_of(node, ent, pe, sc, min(sc.deadline_at, ctx.clock() + node["deadline"] * ctx.scale))
        if await d.land(node, entry, sc, seen):
            raise Frontier([(node["name"], sc.item)])
        seen = [(n, d.landed_outcome(n, o), d.landed_reasons(n, r)) for n, o, r in seen]
        outcome, reasons = V.fold_dispatch([o for _, o, _ in seen], node["join"], len(seen))
        if reasons == ["no_dispatch"]:      # name why every request was refused, so the verdict line is not the only clue (record.jsonl holds the full event)
            reasons += d.rejections(ent, pe)[:8]
        if outcome != "pass" and not reasons:
            reasons = [r for _, o, rs in seen if o != "pass" for r in rs][:8]
        done = stop and not seen
        if done:
            outcome, reasons = "pass", ["done"]
        ledger, cmds = d.payload(node["name"])
        rel = ctx.artifacts.put(node["name"], sc.item, entry, {"children": [n for n, _, _ in seen], "ac_commands": cmds, "ledger": ledger})
        return ctx.finish(node, entry, sc, outcome, reasons, rel, elapsed=ctx.clock() - t0, **({"routed": "exit:pass"} if done else {}))
