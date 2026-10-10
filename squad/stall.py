"""The stall cap: a call stops when it keeps retrying one failing step and the failure does not change.

An attempt is one tool call. Its key is the tool and what it acts on: a Bash command, a file path. An attempt that errors
adds one to its key's count when the failure reads the same as the last one for that key; a failure that reads
differently is evolution, so the count restarts at one. An attempt that succeeds clears its key. Total turns are never
counted: a call that works on for a hundred turns with no repeated failure never stalls. Spend and the deadline still bound it.
"""
import re

TAIL = 600
NOISE = (
    (re.compile(r"\b\d+(\.\d+)?\s*(ms|s|sec|secs|seconds|minutes)\b", re.I), "<time>"),   # a duration is noise, a count of failures is not
    (re.compile(r"0x[0-9a-f]+", re.I), "<addr>"),
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I), "<id>"),
    (re.compile(r"/(?:private/)?(?:var/folders|tmp)/\S+"), "<tmp>"),
)
TARGET_KEYS = ("command", "file_path", "path", "pattern", "notebook_path", "url")


def text_of(content):
    """The text of a tool result: a string, or a list of blocks that carry `text`."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") if isinstance(b, dict) else str(getattr(b, "text", "")) for b in content)
    return ""


def key_of(name, tool_input):
    """The step an attempt retries: the tool plus its command or target, whitespace collapsed."""
    d = tool_input if isinstance(tool_input, dict) else {}
    for k in TARGET_KEYS:
        if isinstance(d.get(k), str):
            return f"{name}:{' '.join(d[k].split())[:200]}"
    return str(name)


def signature(content):
    """How a failure reads: the tail of its text with durations, addresses, ids and temp paths removed."""
    s = " ".join(text_of(content).split())[-TAIL:]
    for pat, sub in NOISE:
        s = pat.sub(sub, s)
    return s


class StallWatch:
    """Feed it tool uses and tool results in stream order. `result` returns True when a key has failed `cap` times with the same failure."""

    def __init__(self, cap):
        self.cap = cap
        self.pending = {}     # tool_use_id -> key
        self.runs = {}        # key -> (signature, consecutive count)
        self.tripped = None   # the key that reached the cap

    def use(self, tool_use_id, name, tool_input):
        self.pending[tool_use_id] = key_of(name, tool_input)

    def result(self, tool_use_id, is_error, content):
        key = self.pending.pop(tool_use_id, None)
        if key is None:
            return False
        if not is_error:
            self.runs.pop(key, None)
            return False
        sig = signature(content)
        last, n = self.runs.get(key, (None, 0))
        n = n + 1 if sig == last else 1
        self.runs[key] = (sig, n)
        if n >= self.cap:
            self.tripped = key
            return True
        return False
