"""Load and check config/agent-defaults.yaml. The engine-level keys load per invocation (panel 2026-09-30, ticket #46)."""
import hashlib
import pathlib

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
PATH = ROOT / "config" / "agent-defaults.yaml"
EFFORTS = {"low", "medium", "high", "max"}
MODES = {"default", "acceptEdits", "plan", "dontAsk", "auto", "bypassPermissions"}
MIN_CONTEXT = 100000
MAX_RESUMES = 10
CAPS = ("max_stalls", "max_budget_usd", "deadline_seconds", "max_output_tokens", "context_tokens")


def load(path=PATH):
    return yaml.safe_load(pathlib.Path(path).read_text())


ABSENT = object()   # a key the file leaves out, which a present null is not


def _lookup(cfg, key):
    """The value at `key`, or ABSENT. A one-dot key reads the old single-level lookup, so a file main accepts reads as it
    always did; a deeper key (columns.<column>.max_cards) walks its whole path."""
    if key.count(".") == 1:
        sec, _, name = key.partition(".")
        return cfg.get(sec, {}).get(name, ABSENT) if isinstance(cfg.get(sec), dict) else ABSENT
    v = cfg
    for part in key.split("."):
        if not isinstance(v, dict) or part not in v:
            return ABSENT
        v = v[part]
    return v


def check(cfg):
    """Return a list of defects; empty means the file is sound."""
    from squad import project_settings as P  # the one catalog: this checker and `squad config check` read the same ranges
    bad, o = [], cfg.get("options", {})
    for key, spec in P.CATALOG.items():
        v = _lookup(cfg, key)
        if v is ABSENT:
            if key in P.REQUIRED:
                bad.append(key)
        elif v is None and key not in P.REQUIRED:
            pass    # a present null on an optional key means unset, or (an intnone key) an explicit none, as before
        elif P.value_defect(spec, v, key):
            bad.append(key)
    if cfg.get("hooks", {}).get("log_compaction") is not True:
        bad.append("hooks.log_compaction")
    if "dashboard" in cfg:
        r = cfg["dashboard"].get("reread_seconds") if isinstance(cfg["dashboard"], dict) else None
        if isinstance(r, bool) or not isinstance(r, (int, float)) or r <= 0:
            bad.append("dashboard.reread_seconds")
        d = cfg["dashboard"] if isinstance(cfg["dashboard"], dict) else {}
        if "watch_seconds" in d:                    # ticket #105: how often the server stats the record while a stream is open
            w = d["watch_seconds"]
            if isinstance(w, bool) or not isinstance(w, (int, float)) or w <= 0:
                bad.append("dashboard.watch_seconds")
    if "progress" in cfg:                       # ticket #101: the interval between two progress events of one call; null writes none
        p = cfg["progress"].get("seconds") if isinstance(cfg["progress"], dict) else None
        if p is not None and (isinstance(p, bool) or not isinstance(p, (int, float)) or p < 0):
            bad.append("progress.seconds")
    a = cfg.get("autonomy", {})                  # ticket #180: both keys are plugin defaults; a missing key takes the default
    a = a if isinstance(a, dict) else {}
    if "enabled" in a and not isinstance(a["enabled"], bool):
        bad.append("autonomy.enabled")
    if "max_resumes" in a and (isinstance(a["max_resumes"], bool) or not isinstance(a["max_resumes"], int) or not 0 <= a["max_resumes"] <= MAX_RESUMES):
        bad.append("autonomy.max_resumes")
    if "watch" in cfg:
        w = cfg["watch"] if isinstance(cfg["watch"], dict) else {}
        if not isinstance(w.get("dashboard"), bool):
            bad.append("watch.dashboard")       # ticket #183: open the dashboard on a new run
        if "monitor" in w and not isinstance(w["monitor"], bool):
            bad.append("watch.monitor")         # ticket #184: the run-scoped monitor
        if "monitor_seconds" in w and (isinstance(w["monitor_seconds"], bool) or not isinstance(w["monitor_seconds"], (int, float)) or not 5 <= w["monitor_seconds"] <= 600):
            bad.append("watch.monitor_seconds")
    from squad import prices
    bad += prices.defects(cfg.get("prices"))
    bad += [f"roles.{r}.{k}" for r, v in cfg.get("roles", {}).items() for k in v if k not in o]
    return bad


def autonomy(cfg):
    """The autonomy setting as run_start records it: {enabled, max_resumes}. A missing key takes the plugin default."""
    a = cfg.get("autonomy") or {}
    return {"enabled": a.get("enabled", True), "max_resumes": a.get("max_resumes", 3)}


AUTONOMY_FRAGMENT = ROOT / "prompts" / "_autonomy.md"


def owner_only(text=None):
    """The owner-only list, parsed from the line `Owner-only list: a; b; c.` of the autonomy fragment, or of `text`. The one list the fragment and the plugin commands share."""
    import re
    m = re.search(r"^Owner-only list: (.+)\.$", text if text is not None else AUTONOMY_FRAGMENT.read_text(), re.M)
    return [x.strip() for x in m.group(1).split(";")] if m else []


def call_env(cfg):
    """The per-call environment that carries the two token limits, the call deadline and the child-session mark. The adapter merges it into ClaudeAgentOptions.env.
    The deadline is exported so it reaches the processes a call starts: a gate the call takes (`serial.sh`) reads it as the cap it must
    stop at, so a call that outlives its own deadline stops its children instead of leaving them to hold a machine-wide lock (ADR-0051)."""
    lim = cfg["limits"]
    env = {k: str(v) for k, v in cfg.get("env", {}).items()}
    env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = str(lim["max_output_tokens"])
    env["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] = str(lim["context_tokens"])
    env["SG_CALL_DEADLINE_SECONDS"] = str(lim["deadline_seconds"])
    env["SG_CHILD_SESSION"] = "1"   # the loop merges, never a call: the gh door (`ghcall.sh`) refuses a PR merge where this is set
    return env


def digest(path=PATH):
    """The sha256 of the file bytes. run_start records it, so a later change to the file is visible against a run."""
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


ENGINE_OPTIONS = ("permission_mode", "setting_sources", "plugins", "system_prompt", "disallowed_tools", "strict_mcp_config")
FLAGS = ("enable_file_checkpointing", "include_partial_messages", "include_hook_events", "forward_subagent_text")


def engine_view(cfg):
    """The engine-level keys, as run_start records them. Node-level keys are a scaffold and live in the graph."""
    o = cfg["options"]
    view = {"options": {k: o[k] for k in ENGINE_OPTIONS if k in o}, "limits": {k: cfg["limits"][k] for k in ("max_output_tokens", "context_tokens")},
            "env": call_env(cfg), "hooks": cfg["hooks"], "auth": cfg["auth"], "log": cfg["log"]}
    if "prices" in cfg:
        view["prices"] = cfg["prices"]       # run_start records the rates a priced call was charged at
    if "dashboard" in cfg:
        view["dashboard"] = {k: cfg["dashboard"][k] for k in ("reread_seconds", "watch_seconds") if k in cfg["dashboard"]}   # the page reads the re-read interval; a record from before ticket #105 lacks the watch step and the server uses its own default
    if "progress" in cfg:
        view["progress"] = {"seconds": cfg["progress"]["seconds"]}                   # how often a call in flight wrote its progress
    return view


def watch_view(cfg):
    """The watch setting, as run_start records it. A file with no `watch` block means the default: the dashboard opens."""
    w = cfg.get("watch") or {}
    return {"dashboard": w.get("dashboard", True), "monitor": w.get("monitor", True), "monitor_seconds": w.get("monitor_seconds", 30)}


def sdk_kwargs(cfg, root=ROOT):
    """ClaudeAgentOptions keywords for the engine-level keys. A relative path resolves against root. The per-call node keys are not here."""
    o, root = cfg["options"], pathlib.Path(root)
    kw = {"permission_mode": o["permission_mode"], "setting_sources": list(o["setting_sources"]), "disallowed_tools": list(o["disallowed_tools"]),
          "strict_mcp_config": bool(o["strict_mcp_config"]), "env": call_env(cfg),
          "plugins": [{"type": p["type"], "path": str(root / p["path"])} for p in o["plugins"]]}
    sp = dict(o["system_prompt"])
    if "append_file" in sp:
        sp["append"] = (root / sp.pop("append_file")).read_text()
        if autonomy(cfg)["enabled"]:       # ticket #181: with the setting on, the contract carries the autonomy fragment; off leaves it out
            sp["append"] += "\n" + (root / "prompts" / "_autonomy.md").read_text()
    kw["system_prompt"] = sp
    kw.update({k: True for k in FLAGS if o.get(k)})
    kw.update({k: o[k] for k in ("fallback_model", "thinking") if o.get(k) is not None})
    if o.get("effort"):     # the engine's default effort: a node that names none runs at this level, never at the model's own default
        kw["effort"] = o["effort"]
    if o.get("verbatim_prompts"):
        kw["verbatim_prompts"] = True
    return kw
