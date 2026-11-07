"""A provider refusal that names a reset time writes the account's limited marker.

The loop already skips a model while the shared marker of its provider is ahead of now: `codex-limited` closes every
gpt- model, `agy-limited` every gemini- one, and the launcher turns a closed marker into the run's `spent_models`.
Each file holds one epoch, the format `review.sh` and `codex-reset.py` already write. The engine records
`model_refused` for every entry the provider refused; this module makes that record leave the same marker behind, so
the next launch and the in-run chain skip the spent account until the reset the refusal itself named (#572).

A refusal with no readable reset time writes nothing: the chain then reads as it did before, and a wrong marker is
never guessed.
"""
import os
import pathlib
import re
import time
import zoneinfo
from datetime import datetime, timedelta

# The account-wide quota a model answers to, exactly as mkreq.sh and launch.py read it.
MARKERS = (("gpt-", "codex-limited"), ("gemini-", "agy-limited"))
_RESET = re.compile(r"\bresets?\s+(?:at\s+)?(\d{1,2}):(\d{2})\s*([ap]m)?\b", re.IGNORECASE)
_ZONE = re.compile(r"\(([A-Za-z][A-Za-z0-9_+-]*(?:/[A-Za-z0-9_+/-]+)?)\)")


def marker(model):
    """The shared marker that closes `model`, or None when its provider writes none. The prefix is read after the
    route's `claude-` alias, so `claude-gpt-5.6-terra` is the gateway account a bare `gpt-5.6-terra` names."""
    bare = (model or "").removeprefix("claude-")
    return next((name for prefix, name in MARKERS if bare.startswith(prefix)), None)


def reset_epoch(text, now=None):
    """The epoch the refusal's own `resets <time>` names, or None when it names none or an impossible clock. A time
    already past is the next time that clock comes round. The zone in the sentence wins; else the local clock is read."""
    m = _RESET.search(text or "")
    if not m:
        return None
    hour, minute, ampm = int(m.group(1)), int(m.group(2)), (m.group(3) or "").lower()
    if hour > 23 or minute > 59 or (ampm and not 1 <= hour <= 12):
        return None
    if ampm:
        hour = hour % 12 + (12 if ampm == "pm" else 0)
    now = time.time() if now is None else now
    z = _ZONE.search((text or "")[m.end():])
    try:
        at = datetime.fromtimestamp(now, zoneinfo.ZoneInfo(z.group(1)) if z else None)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError, OSError):
        at = datetime.fromtimestamp(now)
    at = at.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if at.timestamp() <= now:
        at += timedelta(days=1)
    return int(at.timestamp())


def write(model, error, shared=None, now=None):
    """Write the limited marker a refused `model` answers to, until the reset its error names. Return the marker path,
    or None when the model has no marker, the text names no reset, or no shared directory is known or writable: a
    state write that fails never fails the run."""
    name = marker(model)
    shared = shared or os.environ.get("SG_SHARED")
    if not name or not shared:
        return None
    until = reset_epoch(str(error), now)
    if until is None:
        return None
    path = pathlib.Path(shared) / name
    tmp = path.with_name(f".{name}.{os.getpid()}.tmp")
    try:      # atomic (ADR-0030): a reader never sees a half-written epoch
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(f"{until}\n")
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)
        return None
    return path
