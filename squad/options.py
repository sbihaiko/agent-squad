"""Engine options: how one invocation runs the same graph.

An option may name nodes and may lower a run-block value. It never sets a node's model or budget.
"""
LOWER = {"spend_cap": "spend_cap", "deadline": "deadline", "max_parallel": "max_parallel"}
NODE_OPTIONS = ("stop_after",)
OTHER = ("tier_pin", "expect_graph_hash", "spent_models", "model_hours", "model_order", "model_ctx", "keep_children", "fix_run", "wave_ceiling")
TIERS = ("trivial", "medium", "large")


class OptionError(ValueError):
    pass


def resolve(graph, options):
    """Return (options, run_cfg) for one run. Raise OptionError for a refused option. The graph is never changed."""
    from squad.graph import check_node_names  # lazy: the free-slot count imports this module and never resolves an option
    opts, cfg = dict(options or {}), dict(graph.run)
    for k in opts:
        if k not in (*LOWER, *NODE_OPTIONS, *OTHER):
            raise OptionError(f"{k} is not an engine option: a node's model and budget change only by a graph edit or an applied proposal")
    for k in NODE_OPTIONS:
        if k in opts:
            bad = check_node_names(graph.doc, [opts[k]])
            if bad:
                raise OptionError(str(bad[0]))
    for k in LOWER:
        if k in opts:
            v = opts[k]
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0:
                raise OptionError(f"{k} must be a positive number")
            if cfg[k] is not None and v > cfg[k]:     # a null run deadline has no ceiling to lower from
                raise OptionError(f"{k} {v} is above the run block value {cfg[k]}: an option may only lower it")
            cfg[k] = v
    mins = [n["budget"]["spend"]["min"] for n in graph.nodes.values() if n["kind"] == "agent"]
    if "spend_cap" in opts and mins and cfg["spend_cap"] < max(mins):
        raise OptionError(f"spend_cap {cfg['spend_cap']} is below an agent's spend minimum {max(mins)}")
    if "tier_pin" in opts and opts["tier_pin"] not in TIERS:
        raise OptionError(f"tier_pin must be one of {', '.join(TIERS)}")
    if "spent_models" in opts and not (isinstance(opts["spent_models"], list) and all(isinstance(m, str) and m for m in opts["spent_models"])):
        raise OptionError("spent_models must be a list of model ids")
    if "model_hours" in opts and not (isinstance(opts["model_hours"], dict) and all(isinstance(m, str) and m and isinstance(w, str) for m, w in opts["model_hours"].items())):
        raise OptionError("model_hours must map a model id to an hours window (\"HH-HH\", or the day and UTC grammar of squad/modelreg.py)")
    if "model_order" in opts and not (isinstance(opts["model_order"], list) and all(isinstance(m, str) and m for m in opts["model_order"])):
        raise OptionError("model_order must be a list of model ids")
    if "model_ctx" in opts and not (isinstance(opts["model_ctx"], dict) and all(isinstance(m, str) and m and isinstance(w, str) for m, w in opts["model_ctx"].items())):
        raise OptionError("model_ctx must map a model id to its context window (\"128k\", \"1m\", or a token count)")
    if "keep_children" in opts and not isinstance(opts["keep_children"], bool):
        raise OptionError("keep_children must be true or false")
    if "fix_run" in opts and not isinstance(opts["fix_run"], bool):
        raise OptionError("fix_run must be true or false")
    if "wave_ceiling" in opts and not (isinstance(opts["wave_ceiling"], str) and opts["wave_ceiling"]):
        raise OptionError("wave_ceiling must name the file that holds the wave's ceiling epoch")
    if "expect_graph_hash" in opts and opts["expect_graph_hash"] != graph.hash:
        raise OptionError(f"graph hash mismatch: expected {opts['expect_graph_hash']}, the loaded graph is {graph.hash}. Nothing ran")
    return opts, cfg
