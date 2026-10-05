"""Validate a spawn request against a dispatch envelope (ADR-0113 rules 3 to 5).

Code checks every value. A value may only narrow. A request over any limit is rejected whole:
no clamp, no partial apply. A model never supplies an identity (rule 4).
"""
from squad import skills

REQUEST_FIELDS = {"prompt", "skill", "model", "effort", "tools", "turns", "spend", "deadline", "workspace", "inputs", "after", "task"}
IDENTITY_FIELDS = {"id", "key", "child", "name", "request_key", "parent", "depth"}


def normalize_request(dispatch, raw, vendor=skills.VENDOR):
    """Fill the optional fields from the envelope. Return (request, errors)."""
    errs = [f"model_supplied_identity:{k}" for k in raw if k in IDENTITY_FIELDS]
    errs += [f"unknown_field:{k}" for k in raw if k not in REQUEST_FIELDS and k not in IDENTITY_FIELDS]
    if not isinstance(raw.get("prompt"), str) or not raw.get("prompt"):
        errs.append("missing_prompt")
    skill = raw.get("skill")
    pinned = {s["name"]: s.get("version") for s in dispatch["allow"]["skills"]}
    if isinstance(skill, str):
        skill = {"name": skill}
    if isinstance(skill, dict) and "version" not in skill:   # no pin in the request: use the allow pin, else the current hash (ADR-0130)
        skill = {**skill, "version": pinned.get(skill.get("name")) or skills.hash_of(skill.get("name"), vendor)}
    if not isinstance(skill, dict) or not skill.get("name"):
        errs.append("missing_skill")
        skill = {"name": "", "version": ""}
    after = raw.get("after", [])
    if not isinstance(after, list) or any(not isinstance(i, int) or isinstance(i, bool) or i < 0 for i in after):
        errs.append("after_malformed")
        after = []
    a, b = dispatch["allow"], dispatch["budget"]
    req = {
        "prompt": raw.get("prompt", ""), "skill": {"name": skill["name"], "version": skill.get("version", "")},
        "model": raw.get("model", a["models"][0]), "effort": raw.get("effort", a["efforts"][0]),
        "tools": list(raw.get("tools", a["tools"])), "turns": raw.get("turns", b["turns"]["max"]),
        "spend": raw.get("spend", b["spend"]["max"]), "deadline": raw.get("deadline", dispatch["deadline"]),
        "workspace": raw.get("workspace", a["workspaces"][0]), "inputs": list(raw.get("inputs", [])),
        "after": list(after),   # indexes of earlier requests in the same list; the engine turns them into child names (ADR-0113, 2026-10-01)
        "task": raw.get("task"),   # the work item this child advances, a string or None; a task key narrows nothing, so no limit rule (ADR-0113, #98)
    }
    return req, errs


def limit_errors(req, disp, depth, parent_tools, parent_spend_left, parent_time_left=None, vendor=skills.VENDOR):
    """Return the rules a normalized request breaks. An empty list means it fits the envelope."""
    a, b = disp["allow"], disp["budget"]
    e = []
    if depth > disp["max_depth"]:
        e.append("depth_over_max")
    if req["model"] not in a["models"]:
        e.append(f"model_outside_allow:{req['model']}")
    if req.get("effort") and req["effort"] not in a["efforts"]:
        e.append("effort_outside_allow")
    for t in req["tools"]:
        if t not in a["tools"]:
            e.append(f"tool_outside_allow:{t}")
        elif parent_tools is not None and t not in parent_tools:
            e.append(f"tool_outside_parent:{t}")
    allowed = {x["name"]: x.get("version") for x in a["skills"]}
    name, version = req["skill"]["name"], req["skill"]["version"]
    if name not in allowed or (allowed[name] and allowed[name] != version):
        e.append("skill_outside_allow")
    elif version != skills.hash_of(name, vendor):   # a pin that differs from VERSIONS.json is rejected whole (ADR-0130)
        e.append(f"skill_pin_mismatch:{name}")
    if not b["turns"]["min"] <= req["turns"] <= b["turns"]["max"]:
        e.append("turns_outside_budget")
    if not b["spend"]["min"] <= req["spend"] <= b["spend"]["max"]:
        e.append("spend_outside_budget")
    if req["spend"] > parent_spend_left:
        e.append("spend_over_remainder")
    if req["deadline"] > disp["deadline"]:
        e.append("deadline_over_dispatch")
    if parent_time_left is not None and req["deadline"] > parent_time_left:
        e.append("deadline_over_parent")
    if req["workspace"] not in a["workspaces"]:
        e.append("workspace_outside_allow")
    return e


def validate_request(disp, raw, depth, parent_tools, parent_spend_left, parent_time_left=None, vendor=skills.VENDOR):
    """Return (normalized request, errors). `vendor` is the engine's vendor/skills."""
    req, errs = normalize_request(disp, raw, vendor)
    return req, errs + limit_errors(req, disp, depth, parent_tools, parent_spend_left, parent_time_left, vendor)
