"""The operator session's model calls, folded from the Claude Code transcripts of one working directory (#1756).

The Spend panel read two sources, the run records and the token usage ledger of the loop scripts. The Claude Code
session that drives the loop by hand, and the agents and workflows it starts, write to neither. Claude Code writes each
assistant message of such a session to a JSONL transcript under the project's folder in the user home, subagent
transcripts in subfolders, and that is the third source.

The fold is pure and reads lines, never a file: `usage_rows` turns transcript lines into rows of model, tokens and call
time, and nothing else leaves it -- no prompt, no answer, no working directory. `calls` drops a row a loop source
already names, one row per loop call at most. `Cache` is the bounded read: a folder whose stamps did not change is not
walked again, a file whose stamp did not change is never parsed again, and the fold is kept between polls. A message
read twice, a resumed session that copies it into a new file, and a compaction that copies it again are one call: one
message id folds to one row.
"""
import json
import os
import pathlib

LOOP_MATCH_S = 300   # the slack around a loop call's own span: a transcript call and a loop call of the same model and counts are the same call within it


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
    in, out} where `in` counts the cache reads (the ledger's form), or {t0, model, tokens_in, tokens_out}, plus the optional
    `t1` that ends the span the call ran in. A row is the same call when the canonical model and the output count match, the
    row's input plus cache reads (or its input alone) is the loop call's input, and the row's time lies within LOOP_MATCH_S
    of the call's span (`t0` when it names no `t1`); a time a side lacks matches nothing. One loop call hides at most one
    row, so two distinct calls that share a model and its counts both stay: the match is a heuristic, and the over-match it
    can still make is named here rather than silently shrinking a busy day. `canon` maps a raw spelling to its canonical id
    (the hub passes its one resolver, ADR-0147)."""
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
        e = c.get("t1", t)
        if not isinstance(m, str) or not isinstance(i, int) or not isinstance(o, int):
            continue
        start = t if isinstance(t, (int, float)) else None
        end = e if isinstance(e, (int, float)) else start
        marks.append([canon(m), i, cr if isinstance(cr, int) else 0, o, start, end, False])
    out = []
    for r in rows:
        for mark in marks:
            m, i, _cr, o, start, end, used = mark
            if used or canon(r["model"]) != m or r["out"] != o:
                continue
            if not (r["in"] + r["cache"] == i or r["in"] == i):
                continue
            if (start is None) != (r["t0"] is None):
                continue
            if start is not None and not start - LOOP_MATCH_S <= r["t0"] <= end + LOOP_MATCH_S:
                continue
            mark[6] = True
            break
        else:
            out.append(r)
    return out


class Cache:
    """The bounded read of one project folder: each transcript is folded once per stamp (mtime and size), a changed file is
    folded again in full (a rewrite folds like a fresh read), a vanished file is dropped, and the rows are kept between
    polls. A poll that finds no directory and no file changed walks nothing: the folder's own stamp and its subfolders'
    stamps bound the walk as the file stamps bound the parse. `parsed` counts the files folded so far and `walks` the
    directory walks, so a test proves an unchanged folder parses nothing and walks nothing."""

    def __init__(self):
        self.files = {}   # path: [(mtime_ns, size), rows]
        self.dirs = {}    # path: mtime_ns, every folder the last walk saw, the root included
        self.root = None
        self.parsed = 0
        self.walks = 0

    def _walk_needed(self):
        """True when the last walk's folders may have gained or lost a file: a folder's mtime moves on a create and a delete,
        never on an append, which the file stamps catch."""
        if not self.dirs:
            return True
        for d, m in self.dirs.items():
            try:
                if os.stat(d).st_mtime_ns != m:
                    return True
            except OSError:
                return True
        return False

    def _walk(self, root):
        try:
            found = sorted(str(p) for p in pathlib.Path(root).rglob("*.jsonl"))
        except OSError:
            found = []
        dirs = {}
        for d in [root] + [os.path.dirname(f) for f in found]:
            while d and d.startswith(root) and d not in dirs:   # every folder from the file up to the root
                try:
                    dirs[d] = os.stat(d).st_mtime_ns
                except OSError:
                    pass
                d = os.path.dirname(d)
        self.dirs = dirs
        self.walks += 1
        return found

    def read(self, folder):
        """Every transcript under `folder` (a subagent's sits in a subfolder), one row per message id across all of them."""
        root = str(folder)
        if self.root != root:
            self.root, self.files, self.dirs = root, {}, {}
        if self._walk_needed():
            found = self._walk(root)
            here = set(found)
            for k in [k for k in self.files if k not in here]:   # a file that is gone is never kept
                del self.files[k]
        else:
            found = list(self.files)
        for f in found:
            try:
                st = os.stat(f)
            except OSError:
                continue
            stamp = (st.st_mtime_ns, st.st_size)
            ent = self.files.get(f)
            if ent and ent[0] == stamp:
                continue
            try:
                lines = pathlib.Path(f).read_text(errors="replace").splitlines()
            except OSError:
                continue
            self.files[f] = [stamp, usage_rows(lines)]
            self.parsed += 1
        rows, seen = [], set()
        for k in sorted(self.files):
            for r in self.files[k][1]:
                if r["id"] is not None:
                    if r["id"] in seen:
                        continue
                    seen.add(r["id"])
                rows.append(r)
        return rows
