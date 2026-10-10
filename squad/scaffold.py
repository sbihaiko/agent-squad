"""Authoring-time scaffold: one agent node from the defaults file and a role's prompt (panel 2026-09-30, ticket #46, #45).

The node-level keys are copied into the graph document, so the graph hash covers them and a later edit to the
defaults file never changes a run that already has a graph.
"""
from squad import defaults

MIN_SPEND, MIN_STALLS = 0.05, 1


class ScaffoldError(ValueError):
    pass


def agent_node(cfg, role, name=None, root=defaults.ROOT):
    """Return a schema-valid agent node for `role`. The role file supplies `instructions`. A role with no entry is refused."""
    if role not in cfg["roles"]:
        raise ScaffoldError(f"no role {role!r} in the defaults file; roles: {', '.join(sorted(cfg['roles']))}")
    o, lim, r = cfg["options"], cfg["limits"], {**cfg["options"], **cfg["roles"][role]}
    prompt = root / "prompts" / f"{role}.md"
    if not prompt.exists():
        raise ScaffoldError(f"no prompt file {prompt}")
    denied = set(r["disallowed_tools"])
    tools = [t for t in r["allowed_tools"] if t not in denied]
    return {"name": name or role, "kind": "agent", "model": o["model"], "effort": o["effort"], "tools": tools,
            "budget": {"stalls": {"min": MIN_STALLS, "max": lim["max_stalls"]}, "spend": {"min": MIN_SPEND, "max": lim["max_budget_usd"]}},
            "deadline": lim["deadline_seconds"], "instructions": prompt.read_text().strip()}
