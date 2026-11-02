"""Masks credential-shaped text before a reader takes it: a token by its shape, a secret by the name it is assigned to.

`safe_cut` keeps a token from being split across two reads: bytes are released only up to the last whitespace, and a tail
with no whitespace waits for more bytes, up to HOLD of them.
"""
import re

MARK = "[redacted]"
HOLD = 1024
TOKENS = re.compile(
    r"sk-ant-[A-Za-z0-9_-]{10,}|sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"
    r"|AKIA[0-9A-Z]{16}|xox[abposr]-[A-Za-z0-9-]{10,}|Bearer\s+[A-Za-z0-9._~+/=-]{16,}"
)
ASSIGNED = re.compile(r"((?:api[_-]?key|token|secret|password|passwd)\s*[:=]\s*)\S+", re.IGNORECASE)


def redact(text):
    text = TOKENS.sub(MARK, text)
    return ASSIGNED.sub(lambda m: m.group(1) + MARK, text)


def safe_cut(raw, final=False):
    """How many leading bytes of `raw` may be released: all of them once final, else up to the last whitespace."""
    if final:
        return len(raw)
    last = max((i + 1 for i, b in enumerate(raw) if b in b" \t\r\n"), default=0)
    return len(raw) if len(raw) - last > HOLD else last
