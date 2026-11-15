"""The operator session's model calls, folded from the Claude Code transcripts of one working directory (#1756).

The Spend panel read two sources, the run records and the token usage ledger of the loop scripts. The Claude Code
session that drives the loop by hand, and the agents and workflows it starts, write to neither. Claude Code writes each
assistant message of such a session to a JSONL transcript under the project's folder in the user home, subagent
transcripts in subfolders, and that is the third source.

The fold is pure and reads lines, never a file: `usage_rows` turns transcript lines into rows of model, tokens and call
time, and nothing else leaves it -- no prompt, no answer, no working directory. `calls` drops a row a loop source
already names. `Cache` is the bounded read: a file whose stamp did not change is never parsed again, and the fold is
kept between polls. A message read twice, a resumed session that copies it into a new file, and a compaction that
copies it again are one call: one message id folds to one row.
"""
import json
import os
import pathlib

LOOP_MATCH_S = 300   # a transcript call and a loop call of the same model and counts are the same call within this span


def _n(v):
    """A token count as the transcript writes it: a non-negative int, else 0."""
    return v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else 0


def _epoch(s):
    """A transcript line's ISO timestamp as epoch seconds, None when it has none or it is not one."""
    import datetime as dt
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except (AttributeError, TypeError, ValueError):
        return None


def usage_rows(lines):
    """The model calls of transcript lines, one row per message id: {"id", "model", "in", "out", "cache", "t0"}. `in` is the
    input that missed the cache plus the cache writes, `cache` the cache reads. A line that is not an assistant message with
    usage, or whose model is synthetic, folds to nothing. A message id read twice folds once. No other field of the line is
    read, so no transcript text leaves the fold."""
    rows, seen = [], set()
    for line in lines:
        if not isinstance(line, str) or '"usage"' not in line:
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        m = d.get("message") if isinstance(d, dict) else None
        if not isinstance(m, dict):
            continue
        u, model = m.get("usage"), m.get("model")
        if not isinstance(u, dict) or not isinstance(model, str) or not model or model.startswith("<"):
            continue
        mid = m.get("id") if isinstance(m.get("id"), str) and m.get("id") else None
        if mid is not None:
            if mid in seen:
                continue
            seen.add(mid)
        rows.append({"id": mid, "model": model,
                     "in": _n(u.get("input_tokens")) + _n(u.get("cache_creation_input_tokens")),
                     "out": _n(u.get("output_tokens")), "cache": _n(u.get("cache_read_input_tokens")),
                     "t0": _epoch(d.get("timestamp"))})
    return rows


def calls(rows, loop=(), canon=None):
    """The rows no loop source names. `loop` is the token usage ledger's calls and the run records' outcomes, each {t0, model,
    in, out} where `in` counts the cache reads (the ledger's form), or {t0, model, tokens_in, tokens_out}. A row is the same
    call when the canonical model and the output count match, the row's input plus cache reads (or its input alone) is the
    loop call's input, and the two times are within LOOP_MATCH_S; a time a side lacks matches nothing. `canon` maps a raw
    spelling to its canonical id (the hub passes its one resolver, ADR-0147)."""
    canon = canon or (lambda m: m)
    marks = []
    for c in loop or ():
        if not isinstance(c, dict):
            continue
        m = c.get("model")
        i = c.get("in", c.get("tokens_in"))
        o = c.get("out", c.get("tokens_out"))
        cr = c.get("cache", c.get("cache_read")) or 0
        t = c.get("t0", c.get("ts"))
        if not isinstance(m, str) or not isinstance(i, int) or not isinstance(o, int):
            continue
        marks.append((canon(m), i, cr if isinstance(cr, int) else 0, o, t if isinstance(t, (int, float)) else None))
    out = []
    for r in rows:
        for m, i, _cr, o, t in marks:
            if canon(r["model"]) != m or r["out"] != o:
                continue
            if not (r["in"] + r["cache"] == i or r["in"] == i):
                continue
            if (t is None) != (r["t0"] is None):
                continue
            if t is not None and abs(r["t0"] - t) > LOOP_MATCH_S:
                continue
            break
        else:
            out.append(r)
    return out


class Cache:
    """The bounded read of one project folder: each transcript is folded once per stamp (mtime and size), a changed file is
    folded again in full (a rewrite folds like a fresh read), a vanished file is dropped, and the rows are kept between
    polls. `parsed` counts the files folded so far, so a test proves an unchanged folder parses nothing."""

    def __init__(self):
        self.files = {}   # path: [(mtime_ns, size), rows]
        self.parsed = 0

    def read(self, folder):
        """Every transcript under `folder` (a subagent's sits in a subfolder), one row per message id across all of them."""
        here = set()
        try:
            found = sorted(pathlib.Path(folder).rglob("*.jsonl"))
        except OSError:
            found = []
        for f in found:
            here.add(str(f))
            try:
                st = os.stat(f)
            except OSError:
                continue
            stamp = (st.st_mtime_ns, st.st_size)
            ent = self.files.get(str(f))
            if ent and ent[0] == stamp:
                continue
            try:
                lines = f.read_text(errors="replace").splitlines()
            except OSError:
                continue
            self.files[str(f)] = [stamp, usage_rows(lines)]
            self.parsed += 1
        for k in [k for k in self.files if k not in here]:   # a file that is gone is never kept
            del self.files[k]
        rows, seen = [], set()
        for k in sorted(self.files):
            for r in self.files[k][1]:
                if r["id"] is not None:
                    if r["id"] in seen:
                        continue
                    seen.add(r["id"])
                rows.append(r)
        return rows
