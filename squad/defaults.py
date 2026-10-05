"""Load and check config/agent-defaults.yaml. The engine-level keys load per invocation (panel 2026-09-30, ticket #46)."""
import hashlib
import pathlib

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
PATH = ROOT / "config" / "agent-defaults.yaml"
EFFORTS = {"low", "medium", "high", "max"}
MODES = {"default", "acceptEdits", "plan", "dontAsk", "auto", "bypassPermissions"}
MIN_CONTEXT = 100000
CAPS = ("max_turns", "max_budget_usd", "deadline_seconds", "max_output_tokens", "context_tokens")


def load(path=PATH):
    return yaml.safe_load(pathlib.Path(path).read_text())


def check(cfg):
    """Return a list of defects; empty means the file is sound."""
    bad, o, lim = [], cfg.get("options", {}), cfg.get("limits", {})
    if o.get("effort") not in EFFORTS:
        bad.append("options.effort")
    if o.get("permission_mode") not in MODES:
        bad.append("options.permission_mode")
    if isinstance(lim.get("context_tokens"), (int, float)) and lim["context_tokens"] < MIN_CONTEXT:
        bad.append("limits.context_tokens")      # probed 2026-09-30: the CLI clamps a smaller window up to 100000
    bad += [f"limits.{k}" for k in CAPS if not isinstance(lim.get(k), (int, float)) or lim[k] <= 0]
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
    run = cfg.get("run", {})
    if run.get("deadline") is not None and run["deadline"] <= 0:
        bad.append("run.deadline")
    bad += [f"run.{k}" for k in ("max_parallel", "spend_cap") if not isinstance(run.get(k), (int, float)) or run[k] <= 0]
    bad += [f"roles.{r}.{k}" for r, v in cfg.get("roles", {}).items() for k in v if k not in o]
    return bad


def call_env(cfg):
    """The per-call environment that carries the two token limits. The adapter merges it into ClaudeAgentOptions.env."""
    lim = cfg["limits"]
    env = {k: str(v) for k, v in cfg.get("env", {}).items()}
    env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = str(lim["max_output_tokens"])
    env["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] = str(lim["context_tokens"])
    return env


def digest(path=PATH):
    """The sha256 of the file bytes. run_start records it, so a later change to the file is visible against a run."""
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


ENGINE_OPTIONS = ("permission_mode", "setting_sources", "plugins", "system_prompt", "disallowed_tools", "strict_mcp_config")
FLAGS = ("enable_file_checkpointing", "include_partial_messages", "include_hook_events", "forward_subagent_text")


def engine_view(cfg):
    """The engine-level keys, as run_start records them. Node-level keys are a scaffold and live in the graph (ADR-0106)."""
    o = cfg["options"]
    view = {"options": {k: o[k] for k in ENGINE_OPTIONS if k in o}, "limits": {k: cfg["limits"][k] for k in ("max_output_tokens", "context_tokens")},
            "env": call_env(cfg), "hooks": cfg["hooks"], "auth": cfg["auth"], "log": cfg["log"]}
    if "dashboard" in cfg:
        view["dashboard"] = {k: cfg["dashboard"][k] for k in ("reread_seconds", "watch_seconds") if k in cfg["dashboard"]}   # the page reads the re-read interval; a record from before ticket #105 lacks the watch step and the server uses its own default
    if "progress" in cfg:
        view["progress"] = {"seconds": cfg["progress"]["seconds"]}                   # how often a call in flight wrote its progress (ticket #101)
    return view


def sdk_kwargs(cfg, root=ROOT):
    """ClaudeAgentOptions keywords for the engine-level keys. A relative path resolves against root. The per-call node keys are not here."""
    o, root = cfg["options"], pathlib.Path(root)
    kw = {"permission_mode": o["permission_mode"], "setting_sources": list(o["setting_sources"]), "disallowed_tools": list(o["disallowed_tools"]),
          "strict_mcp_config": bool(o["strict_mcp_config"]), "env": call_env(cfg),
          "plugins": [{"type": p["type"], "path": str(root / p["path"])} for p in o["plugins"]]}
    sp = dict(o["system_prompt"])
    if "append_file" in sp:
        sp["append"] = (root / sp.pop("append_file")).read_text()
    kw["system_prompt"] = sp
    kw.update({k: True for k in FLAGS if o.get(k)})
    kw.update({k: o[k] for k in ("fallback_model", "thinking") if o.get(k) is not None})
    if o.get("verbatim_prompts"):
        kw["verbatim_prompts"] = True
    return kw
