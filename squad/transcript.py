"""The token counts of Claude Code's transcripts, for the profile's fallback (SPEC 1035, #1039).

A transcript is one JSON object per line. An assistant line carries `message.id`, `message.model` and `message.usage`.
The caller names the files; this module reads only those. A partial or unreadable transcript answers None, never a
half count. A message id counts once, and a `<synthetic>` model is no model.
"""
import json
from pathlib import Path

USAGE_KEYS = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")


def _int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _messages(path):
    """(message id, model, usage) of each assistant message of one transcript. Raises ValueError on a usage object
    missing a count, and on a line that is not JSON (a truncated line is a partial transcript)."""
    out = []
    for i, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines()):
        if not line.strip():
            continue
        d = json.loads(line)
        m = d.get("message") if isinstance(d, dict) else None
        if not isinstance(m, dict) or not isinstance(m.get("usage"), dict):
            continue
        model = m.get("model")
        if not isinstance(model, str) or model.startswith("<"):
            continue
        u = m["usage"]
        if not all(_int(u.get(k)) for k in USAGE_KEYS):
            raise ValueError(f"usage of {path} line {i + 1} lacks a count")
        out.append((m.get("id") or f"{path}:{i}", model, u))
    return out


def transcript_tokens(files, model):
    """(tokens in, tokens out, tokens cache) of one model across these transcripts, or None when a file is partial
    or unreadable, or no message names the model."""
    seen = {}
    try:
        for f in files:
            for mid, m, u in _messages(f):
                seen.setdefault(mid, (m, u))
    except (OSError, ValueError):
        return None
    hits = [u for m, u in seen.values() if m == model]
    if not hits:
        return None
    return (sum(u["input_tokens"] for u in hits),
            sum(u["output_tokens"] for u in hits),
            sum(u["cache_read_input_tokens"] + u["cache_creation_input_tokens"] for u in hits))
