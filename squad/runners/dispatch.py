"""Dispatch decisions and landing.

`Dispatch` owns the spawned children, their held reservations, the landed children, and the held-out
baseline. It decides spawn requests ("decide these requests") and lands finished children ("land these
children"). It reads and writes the spawn, land, and ledger events. The engine runs the children's sessions.
"""
import re
import subprocess

import anyio

from squad import actions, envelope, gates, git, node_commit, session, workcmd
from squad import verdict as V
from squad.engine_errors import Frontier

REVIEW_TICK_S = 60.0                      # a review in flight writes one `review_progress` event at least this often (the heartbeat's quiet window is 5 min)
REVIEW_NONE_EXIT = gates.REVIEW_NONE_EXIT  # the review command's "no review here": the child lands as today
REVIEW_FIX_EXIT = gates.REVIEW_FIX_EXIT    # the review command's "fix": the run starts the child's next entry in its worktree (#570)
REVIEW_RETRY_EXIT = gates.REVIEW_RETRY_EXIT  # the review command's "try again": no verdict; the run tries again within the gate's deadline (#635)
REVIEW_RETRY_PAUSE_S = 60.0               # the pause between two tries of one round, inside the gate's deadline


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
        # The engine's own default effort (the configuration's `options.effort`) is what a child that names none runs at:
        # an adapter with no such option leaves the allow list's first entry to lead, as before.
        req, errs = envelope.validate_request(disp, raw, depth, parent_tools, min(spend_left, self.e.budget.available()), time_left, self.e.vendor,
                                            default_effort=getattr(self.e.adapter, "extra_options", {}).get("effort"))
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

    def last(self, name):
        """The outcome event of a child's last entry: 1, or the last fix round its review asked for (#570). None before entry 1."""
        n = 1
        while self.e.outcomes.recorded(name, n + 1):
            n += 1
        return self.e.outcomes.event(name, n)

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
            review, rounds, why = (await self.rounds(node, name, d, path, head_before, sc)
                                   if node.get("review") and path.exists() else (None, 0, None))
            if not path.exists():      # a passed writer whose worktree is gone: its work cannot land, and the record says so
                r = actions.ActionResult(False, ["land_workspace_missing"], head_before)
            elif why:                  # a held child is not merged: its worktree stays, with every fix round on its branch
                r = actions.ActionResult(False, why, head_before)
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
                                                   effect={"name": "review" if r.reasons and r.reasons[0].startswith("review_") else "merge_land",
                                                           "before": head_before, "head": r.head, **r.extra,
                                                           **({"review": review, "rounds": rounds} if review else {})})   # `before` is read here: the landing check replaces the merge's result
        return False

    async def rounds(self, node, name, d, path, base, sc):
        """Review one passed child, and fix it while its review asks (#570). Round n reviews the head of entry n; a `fix`
        verdict starts entry n+1 of the same child in its worktree with the full review, and its new head is round n+1.
        The command decides fix or hold (its stall rule, ADR-0134); code offers a round only when it already holds the next
        entry's grant from the run budget (ADR-0108), and there is no cap on rounds. Each round is a `review_round` event;
        a resume replays the recorded rounds and fix entries and reviews only a head with no recorded round (ADR-0107).
        Return (the last review, the rounds, the reasons that hold the child or None)."""
        e, n = self.e, 1
        while True:
            fund, rec = None, next((x for x in e.record.events if x["event"] == "review_round" and x["node"] == name and x["round"] == n), None)
            if rec is None:
                fund = e.budget.hold(self.children[name]["definition"]["spend"])
                review = await self.tries(node, name, d, path, base, n, fund is not None)
                if review["verdict"] == "fix" and fund is None:      # a fix the run did not offer is a hold
                    review["verdict"] = "hold"
                rec = e.record.append("review_round", node=name, round=n, head=git.head(path), verdict=review["verdict"],
                                      offered=fund is not None, review=review)
            review = rec["review"]
            if rec["verdict"] != "fix":
                if fund is not None:
                    e.budget.release(fund)
                if rec["verdict"] == "deadline":
                    return review, n, ["review_deadline"]
                if rec["verdict"] == "hold":
                    return review, n, ["review_held", *([] if rec["offered"] else ["review_fix_unfunded"])]
                return review, n, None
            out = e.outcomes.event(name, n + 1)
            if out is None and any(x["event"] == "call_start" and x["node"] == name and x["entry"] == n + 1 for x in e.record.events):
                if fund is not None:
                    e.budget.release(fund)
                cap = self.children[name]["reservation"]      # a fix entry that crashed mid-session is not run again under its grant
                out = e.runs.outcome(name, "fail", ["crashed", "launched_no_outcome"], None, {"cap": cap}, n + 1)
                e.budget.unmeasured += cap
            elif out is None:
                if fund is not None:
                    self.held[name] = fund      # the fix entry's call takes this grant
                out = await e.runs.fix(node, name, n + 1, review, sc.deadline_at)
            elif fund is not None:
                e.budget.release(fund)
            if out["outcome"] != "pass":
                return review, n, ["review_fix_failed", *out["reasons"]]
            n += 1

    async def tries(self, node, name, d, path, base, n, offer):
        """Review round n until it gets a verdict (#635). A `retry` (no verdict: the chain wrote none, or the slot wait passed
        its bound) is a `review_try` event, and the round tries again after a pause. All tries of the round share the gate's
        one deadline; when it runs out, the verdict is `deadline` and the child is held, never merged unreviewed.
        A resume counts the recorded tries against the deadline (ADR-0107)."""
        e = self.e
        cap = e.nodes[node["review"]]["params"]["deadline"] * e.scale
        done = [x for x in e.record.events if x["event"] == "review_try" and x["node"] == name and x["round"] == n]
        spent, k = sum(x["review"].get("elapsed") or 0 for x in done), len(done)
        last = done[-1]["review"] if done else {"findings": "", "stderr": ""}
        while spent < cap:
            review = await self.review(node, name, d, path, base, n, offer, cap - spent)
            if review["verdict"] != "retry":
                return review
            k, spent, last = k + 1, spent + review["elapsed"], review
            e.record.append("review_try", node=name, round=n, review=review, **{"try": k})
            pause = min(REVIEW_RETRY_PAUSE_S, cap - spent)
            if pause > 0:
                await anyio.sleep(pause)
                spent += pause
        return {**last, "verdict": "deadline"}

    async def review(self, node, name, d, path, base, n=1, offer=False, deadline=None):
        """Run the dispatch's review gate on one passed child, in its worktree, before its merge. The command is graph data:
        its argv template gets the run id, the child, the child's issue, its worktree and the target HEAD it would land on.
        Its environment names the round (`SQUAD_REVIEW_ROUND`) and whether the run offers a fix round (`SQUAD_REVIEW_FIX`).
        While it runs, a `review_progress` event moves the record, so the heartbeat never reads it as wedged (ADR-0051)."""
        e, gate = self.e, node["review"]
        params = e.nodes[gate]["params"]
        argv = gates.review_argv(params["argv"], {"run_id": e.run_id, "child": name, "issue": self.issue(d), "worktree": path, "base": base})
        env = {"SQUAD_REVIEW_ROUND": str(n), "SQUAD_REVIEW_FIX": "1" if offer else "0"}

        def tick(elapsed):
            anyio.from_thread.run_sync(lambda: e.record.append("review_progress", node=name, gate=gate, elapsed=elapsed))
        cap = params["deadline"] * e.scale if deadline is None else deadline
        return await anyio.to_thread.run_sync(lambda: gates.review_command(argv, str(path), cap, REVIEW_TICK_S, tick, env))

    def issue(self, d):
        """The child's issue: the `Refs #<n>` line of its task or prompt (the line the loop counts as delivery), else the
        first `#<n>` in its task, else in its prompt, else the run's own issue; empty when none is known. A prompt may name a
        blocker before its own issue, so `Refs` wins (#613)."""
        for text in (d.get("task"), d.get("prompt")):
            m = re.search(r"\bRefs #(\d+)", text) if isinstance(text, str) else None
            if m:
                return m.group(1)
        for text in (d.get("task"), d.get("prompt")):
            m = re.search(r"#(\d+)", text) if isinstance(text, str) else None
            if m:
                return m.group(1)
        run = node_commit.issue_of(self.e.g.doc)
        return "" if run is None else str(run)

    def prune(self):
        """Remove the branch and worktree of every child that landed. The work is in the target's history now.

        A run started with the engine option `keep_children` keeps them: a later pass reads each child's worktree again
        after this verdict, as the loop does to enter the child's commits into its run's wave branch and to review the
        child's own diff there (ADR-0145 rule 9). The hold is the run's own recorded option (ADR-0106), never the target's
        shape: a wave run whose target is not the run's (#477) is held all the same, and a resume keeps what the start set.
        The pass that lands the wave frees the trees.
        """
        if self.e.options.get("keep_children") or any(
                (e.get("engine_options") or {}).get("keep_children") for e in self.e.record.events if e["event"] == "run_start"):
            return
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
            out = self.last(name)
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
