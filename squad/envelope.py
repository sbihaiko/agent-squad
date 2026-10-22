"""Validate a spawn request against a dispatch envelope.

Code checks every value. A value may only narrow. A request over any limit is rejected whole:
no clamp, no partial apply. A model never supplies an identity.
"""
from squad import modelreg, skills

REQUEST_FIELDS = {"prompt", "skill", "model", "kind", "effort", "tools", "stalls", "spend", "deadline", "workspace", "inputs", "after", "task"}
IDENTITY_FIELDS = {"id", "key", "child", "name", "request_key", "parent", "depth"}



def no_sdk_route(mid, reg=None):
    """True when the registry knows the spelling and the SDK adapter cannot reach it: no gateway route and no `claude`
    CLI route (an agy-only id). The bundled CLI answers such an id with "issue with the selected model" (#698). Unknown
    passes as data. It lives here, not in modelreg.py, whose loop copy must stay byte-identical."""
    reg = reg or modelreg.default_registry()
    entry = modelreg.model(mid, reg)
    if entry is None:
        return False
    cli = (entry.routes.get("cli") or {}).get("argv") or []
    return "gateway" not in entry.routes and not (cli and cli[0] == "claude")

def inside_workspace(w, roots):
    """Whether a requested workspace is one the author allows or a path below one. The author writes the envelope as a
    directory (`worktrees/child`) and the router names the child's own directory below it (`worktrees/child/gui-26`): a
    string equality read that as outside the allow, and every dispatch run died `workspace_outside_allow` with nothing
    delivered (#55). Compared segment by segment, so `worktrees/childish` is not inside `worktrees/child`; a `..` segment
    is never inside, and an absolute path is not a workspace of this run."""
    if not isinstance(w, str) or not w or w.startswith("/"):
        return False
    parts = [p for p in w.split("/") if p]
    if not parts or ".." in parts:
        return False
    for r in roots:
        root = [p for p in str(r).split("/") if p]
        if root and parts[:len(root)] == root:
            return True
    return False


def normalize_request(dispatch, raw, vendor=skills.VENDOR, default_effort=None):
    """Fill the optional fields from the envelope. Return (request, errors). A key outside REQUEST_FIELDS is ignored: it narrows nothing, and one model's extra word must not cost the run its dispatch."""
    errs = [f"model_supplied_identity:{k}" for k in raw if k in IDENTITY_FIELDS]
    if not isinstance(raw.get("prompt"), str) or not raw.get("prompt"):
        errs.append("missing_prompt")
    skill = raw.get("skill")
    pinned = {s["name"]: s.get("version") for s in dispatch["allow"]["skills"]}
    if isinstance(skill, str):
        skill = {"name": skill}
    if isinstance(skill, dict) and "version" not in skill:   # no pin in the request: use the allow pin, else the current hash
        skill = {**skill, "version": pinned.get(skill.get("name")) or skills.hash_of(skill.get("name"), vendor)}
    if not isinstance(skill, dict) or not skill.get("name"):
        errs.append("missing_skill")
        skill = {"name": "", "version": ""}
    after = raw.get("after", [])
    if not isinstance(after, list) or any(not isinstance(i, int) or isinstance(i, bool) or i < 0 for i in after):
        errs.append("after_malformed")
        after = []
    a, b = dispatch["allow"], dispatch["budget"]
    kind = raw.get("kind") or "coding"   # SPEC 750: a kind names the chain the model comes from; none is coding
    if "kind_models" in a and "model" in raw:
        errs.append("model_supplied")
    # An omitted effort is the engine's default, never the allow list's first entry: the list is an order, and its first
    # entry is the weakest level as often as it is the strongest. A default the envelope does not allow narrows nothing.
    eff = raw.get("effort") or (default_effort if default_effort in a["efforts"] else None) or a["efforts"][0]
    req = {
        "prompt": raw.get("prompt", ""), "skill": {"name": skill["name"], "version": skill.get("version", "")},
        "model": raw.get("model", a["models"][0]) if "kind_models" not in a else (a["kind_models"].get(kind) or [""])[0], "effort": eff,
        "tools": list(raw.get("tools", a["tools"])), "stalls": raw.get("stalls", b["stalls"]["max"]),
        "spend": raw.get("spend", b["spend"]["max"]), "deadline": raw.get("deadline", dispatch["deadline"]),
        "workspace": raw.get("workspace", a["workspaces"][0]), "inputs": list(raw.get("inputs", [])),
        "after": list(after),   # indexes of earlier requests in the same list; the engine turns them into child names
        **({"kind": kind} if "kind_models" in a else {}),
        "task": raw.get("task"),   # the work item this child advances, a string or None; a task key narrows nothing, so no limit rule
    }
    return req, errs


def limit_errors(req, disp, depth, parent_tools, parent_spend_left, parent_time_left=None, vendor=skills.VENDOR):
    """Return the rules a normalized request breaks. An empty list means it fits the envelope."""
    a, b = disp["allow"], disp["budget"]
    e = []
    if depth > disp["max_depth"]:
        e.append("depth_over_max")
    if "kind_models" in a and req["kind"] not in a["kind_models"]:
        e.append(f"kind_outside_allow:{req['kind']}")
    elif "kind_models" in a and not req["model"]:
        e.append(f"kind_has_no_model:{req['kind']}")
    elif "kind_models" not in a and req["model"] not in a["models"]:
        e.append(f"model_outside_allow:{req['model']}")
    elif modelreg.no_tools_route(req["model"]):
        e.append(f"model_takes_no_tools:{req['model']}")
    elif no_sdk_route(req["model"]):
        e.append(f"model_has_no_sdk_route:{req['model']}")
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
    elif version != skills.hash_of(name, vendor):   # a pin that differs from VERSIONS.json is rejected whole
        e.append(f"skill_pin_mismatch:{name}")
    if not b["stalls"]["min"] <= req["stalls"] <= b["stalls"]["max"]:
        e.append("stalls_outside_budget")
    if not b["spend"]["min"] <= req["spend"] <= b["spend"]["max"]:
        e.append("spend_outside_budget")
    if req["spend"] > parent_spend_left:
        e.append("spend_over_remainder")
    if req["deadline"] > disp["deadline"]:
        e.append("deadline_over_dispatch")
    if parent_time_left is not None and req["deadline"] > parent_time_left:
        e.append("deadline_over_parent")
    if not inside_workspace(req["workspace"], a["workspaces"]):
        e.append(f"workspace_outside_allow:requested {req['workspace']!r}, allowed {list(a['workspaces'])}")
    return e


def validate_request(disp, raw, depth, parent_tools, parent_spend_left, parent_time_left=None, vendor=skills.VENDOR, default_effort=None):
    """Return (normalized request, errors). `vendor` is the engine's vendor/skills; `default_effort` is the engine's own
    default effort, what a request that names none runs at."""
    req, errs = normalize_request(disp, raw, vendor, default_effort)
    return req, errs + limit_errors(req, disp, depth, parent_tools, parent_spend_left, parent_time_left, vendor)
