#!/usr/bin/env python3
"""Read the Gemini group of `agy -p /quota`: one line per model group, `name<TAB>Weekly Limit Remaining<TAB>N%<TAB>reset ISO`.
One reader for the hub's q_agy and for review.sh, so the card and the agy-limited marker never disagree on the reset.
usage: agy -p /quota | agyquota.py   prints the epoch when the Gemini weekly limit frees up; nothing when it is not spent."""
import datetime as dt, sys, time


def gemini_window(text):
    """(percent used, reset epoch) of the Gemini group, or None when the text carries no such line."""
    for l in text.splitlines():
        c = l.split("\t")
        if len(c) >= 4 and c[0].startswith("Gemini"):
            try: return 100 - float(c[2].rstrip("%")), dt.datetime.fromisoformat(c[3].strip().replace("Z", "+00:00")).timestamp()
            except ValueError: return None
    return None


def spent_reset(text, now=None):
    """The reset epoch when the Gemini weekly limit is spent and the reset is still ahead, else None."""
    w = gemini_window(text)
    if w and w[0] >= 100 and w[1] > (time.time() if now is None else now): return int(w[1])
    return None


if __name__ == "__main__":
    r = spent_reset(sys.stdin.read())
    if r: print(r)
