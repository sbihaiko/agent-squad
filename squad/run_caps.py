"""The per-run ceilings the hub panel shows: the run's `max_parallel` and each dispatch node's `max_children` and `max_parallel`.

They come from the graph a run uses: the newest live run's own `graph.json`, else the graph `squad/workflows.py` resolves for the
checkout (the owner's `default`, then `dynamic`, then the router seed). The hub never builds that path itself, so it cannot disagree with the engine.
This is not the machine-wide pair (`squad/caps.py`): the two never merge, and the label says which number is which.

Guard: `tests/test_run_caps.py`.
"""
import json
from pathlib import Path

from squad import workflows as W


def _graph_file(checkout, run_dirs, seed):
    live = [Path(d) / "graph.json" for d in run_dirs or ()]
    live = [p for p in live if p.is_file()]
    if live:
        return max(live, key=lambda p: p.stat().st_mtime)
    return W.resolve(None, Path(checkout) / W.SUBDIR, seed)


def label(max_parallel, dispatch):
    """`per run: max_parallel 2 · route: max_children 4, max_parallel 2`: each number named for what it caps."""
    parts = [f"per run: max_parallel {max_parallel}"] if max_parallel is not None else []
    parts += [f"{d['name']}: max_children {d['max_children']}, max_parallel {d['max_parallel']}" for d in dispatch]
    return " · ".join(parts)


def read(checkout, run_dirs=(), seed=W.DYNAMIC_SEED):
    """{source, max_parallel, dispatch: [{name, max_children, max_parallel}], label}, or None when the graph cannot be read."""
    try:
        path = _graph_file(checkout, run_dirs, seed)
        doc = json.loads(path.read_text())
        run = doc.get("run") or {}
        dispatch = [{"name": n.get("name"), "max_children": n.get("max_children"), "max_parallel": n.get("max_parallel")}
                    for n in doc.get("nodes") or [] if n.get("kind") == "dispatch"]
        max_parallel = run.get("max_parallel")
        return {"source": str(path), "max_parallel": max_parallel, "dispatch": dispatch, "label": label(max_parallel, dispatch)}
    except (OSError, ValueError, AttributeError, TypeError, W.WorkflowError):
        return None
