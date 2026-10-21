"""One pure classifier for every `adapter_error` run verdict (issue #698).

A run that dies because a model call produced no result folds one `verdict` with the reason
`adapter_error`. The class of that verdict is a pair (route, cause):

- the **route** is (model, backend). The model is the registry id the resolver names, and the backend is
  the route it names (`cli`, `gateway` or `direct`). It is read through `modelreg.normalize` and never
  through a table here (ADR-0147). An unknown spelling passes through as data with no backend.
- the **cause** is one of `auth`, `quota`, `timeout`, `tool_refusal`, `protocol`, else `unknown`.

A run's cascade (`route`, `main`) carries the same reason with no diagnostic of its own, so a verdict is
one class, read from the root outcome that names a diagnostic. No fix is chosen here: this module only
makes the classes visible.

The record reader takes the valid prefix: a corrupt or half-written record counts the lines before its
first unreadable one and never raises. This module is stdlib only and writes nothing.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from squad import modelreg

CAUSES = ("auth", "quota", "timeout", "tool_refusal", "protocol", "unknown")

# The words a provider or a transport writes, read from the real diagnostics in the run records. Order is
# the rule's order: a quota sentence that also names a tool is a quota, and a tool the hook denied is not an
# authentication failure even though the sentence names a permission.
_QUOTA_WORDS = ("session limit", "weekly limit", "usage limit", "rate limit", "quota", "insufficient", "credit",
                "balance", "payment required", "exhausted", "spend limit")
_TIMEOUT_WORDS = ("timeout", "timed out", "deadline exceeded", "etimedout", "operation timed out")
_TOOL_WORDS = ("tool",)
_REFUSAL_WORDS = ("denied", "refus", "not allowed", "not permitted", "disallow", "blocked")
_AUTH_WORDS = ("unauthor", "unauthenticated", "authentication", "forbidden", "permission", "may not have access",
               "api key", "api_key", "credential", "not logged in", "login required", "invalid token", "expired token")
_PROTOCOL_WORDS = ("protocol", "malformed", "parse error", "parseerror", "invalid response", "invalid json",
                   "json decode", "unexpected end", "framing", "transport", "ceiling", "stream ended",
                   "no resultmessage", "bad request", "content-length")
_STATUS = re.compile(r"\b(\d{3})\b")
_STATUS_CAUSE = {"400": "protocol", "401": "auth", "402": "quota", "403": "auth", "408": "timeout",
                 "422": "protocol", "429": "quota"}
# A status is the provider's own answer only when the sentence says it is one, as `session.provider_error` keeps: a
# number in a message about a frame size or a duration is not an authentication or a quota failure.
_STATUS_MARKERS = ("api error", "upstream", '"status"', "http", "status code", "error code")


@dataclass(frozen=True)
class ErrorClass:
    """One class: the registry id (or the raw spelling), its route, and the cause."""
    model: str
    backend: str
    cause: str

    def as_dict(self) -> dict:
        return {"model": self.model, "backend": self.backend, "cause": self.cause}


def _status_cause(text: str) -> str | None:
    if not any(m in text for m in _STATUS_MARKERS):
        return None
    for status in _STATUS.findall(text):
        cause = _STATUS_CAUSE.get(status)
        if cause:
            return cause
    return None


def cause(text: str | None) -> str:
    """The cause one diagnostic text names, from the closed set `CAUSES`. An empty or unmatched text is `unknown`."""
    low = (text or "").lower()
    if any(w in low for w in _QUOTA_WORDS):
        return "quota"
    if any(w in low for w in _TIMEOUT_WORDS):
        return "timeout"
    if any(w in low for w in _TOOL_WORDS) and any(w in low for w in _REFUSAL_WORDS):
        return "tool_refusal"
    if any(w in low for w in _AUTH_WORDS):
        return "auth"
    if any(w in low for w in _PROTOCOL_WORDS):
        return "protocol"
    return _status_cause(low) or "unknown"


def _text(ev: dict) -> str:
    """The diagnostic text one outcome event carries: `diagnostic.error` first, then its own `error`."""
    diag = ev.get("diagnostic")
    if isinstance(diag, dict) and isinstance(diag.get("error"), str):
        return diag["error"]
    return ev["error"] if isinstance(ev.get("error"), str) else ""


def _raw(ev: dict) -> str:
    """The spelling the record kept: the graph's own `model`, else the registry id a newer record also names."""
    for key in ("model", "model_id"):
        value = ev.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def classify_event(ev: dict, reg=None) -> ErrorClass:
    """The class of one adapter-error outcome event. The model is resolved through the one resolver (ADR-0147)."""
    raw = _raw(ev)
    ref = modelreg.normalize(raw, reg) if raw else None
    model = (ref.id if ref is not None and ref.known else raw) or "unknown"
    backend = ref.route if ref is not None and ref.known and ref.route else "unknown"
    return ErrorClass(model=model, backend=backend, cause=cause(_text(ev)))


def classify_run(events, reg=None) -> list[ErrorClass]:
    """The classes of one run record: one per `adapter_error` verdict, none when the verdict names another reason.

    The class is read from the root failure that carries a diagnostic; the cascade that follows it carries the
    reason alone. A verdict whose record holds no outcome is still one `unknown` class, never a missing run.
    """
    evs = [e for e in events if isinstance(e, dict)]
    folded = next((e for e in evs if e.get("event") == "verdict"), None)
    if folded is None or "adapter_error" not in (folded.get("reasons") or []):
        return []
    outcomes = [e for e in evs if e.get("event") == "outcome" and "adapter_error" in (e.get("reasons") or [])]
    root = next((e for e in outcomes if _text(e)), None) or (outcomes[0] if outcomes else None)
    return [classify_event(root, reg)] if root is not None else [ErrorClass("unknown", "unknown", "unknown")]


def read_prefix(path) -> list[dict]:
    """The events of one record up to its first unreadable line. A missing or unreadable file gives none.

    A half-written record is normal at the moment it is read; the valid prefix is the truth that exists, so
    the scan counts it and goes on.
    """
    try:
        text = Path(path).read_text(errors="replace")
    except OSError:
        return []
    events: list[dict] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            break
        if not isinstance(ev, dict):
            break
        events.append(ev)
    return events


def classify_record(path, reg=None) -> list[ErrorClass]:
    """The classes of the record at `path`."""
    return classify_run(read_prefix(path), reg)


def run_folders(root) -> list[Path]:
    """The run folders that still exist under one root: each `run-*` directory, in name order."""
    try:
        return sorted(p for p in Path(root).glob("run-*") if p.is_dir())
    except OSError:
        return []


def aggregate(folders, reg=None) -> list[dict]:
    """The counts per (model, backend, cause) over the run folders given.

    Every folder's record is read for its valid prefix, so a corrupt or partial record counts what it holds and
    never crashes the scan. Rows are ordered by count, largest first, then by their key.
    """
    counts: dict[ErrorClass, int] = {}
    for folder in folders:
        events = read_prefix(Path(folder) / "record.jsonl")
        for cls in classify_run(events, reg):
            counts[cls] = counts.get(cls, 0) + 1
    rows = [{**cls.as_dict(), "count": n} for cls, n in counts.items()]
    rows.sort(key=lambda r: (-r["count"], r["model"], r["backend"], r["cause"]))
    return rows
