"""The action runner: one git effect in a workspace."""
import subprocess

from squad import actions, git


class ActionRunner:
    async def run(self, ctx, node, entry, sc):
        deadline, bound_by = ctx.call_deadline(node, sc)
        if deadline <= 0:
            return ctx.finish(node, entry, sc, "deadline", ["deadline", bound_by or "run_deadline"])
        logical, eff = node.get("workspace"), node["effect"]
        path, t0 = ctx.workspace_path(logical, sc.item), ctx.clock()
        branch = actions.branch_name(ctx.run_id, logical or "target", sc.item)
        msg = f"squad: {node['name']} ({ctx.run_id})"
        try:
            if eff == "worktree_create":
                r = actions.worktree_create(ctx.target, path, branch, deadline)
                if r.ok:
                    ctx.branches[(logical, sc.item)] = branch
            elif eff == "worktree_revert":
                head = ctx.recorded_head(logical, sc.item)
                r = actions.worktree_revert(path, head, deadline)
            elif eff == "commit":
                r = actions.commit(path, msg, deadline)
            else:
                b = ctx.branches.get((logical, sc.item)) or branch
                r = actions.merge_land(ctx.target, path, b, msg, deadline)
        except subprocess.TimeoutExpired:
            return ctx.finish(node, entry, sc, "deadline", ["deadline"], elapsed=ctx.clock() - t0)
        ws = ctx.ws_snapshot(node, sc) if eff != "merge_land" else None
        extra = {"workspace": ws} if ws else {}
        extra["effect"] = {"name": eff, "head": r.head, **r.extra}
        if eff == "merge_land":
            extra["workspace"] = {"name": logical, "path": str(path), "head": git.head(path), "state": git.state(path)}
        return ctx.finish(node, entry, sc, "pass" if r.ok else "fail", r.reasons, None, elapsed=ctx.clock() - t0, **extra)
