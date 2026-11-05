"""A repeated acceptance failure ends the run as needs-human (#1378, ADR-0151).

Two identical ac-verify failures in a row mean another round cannot satisfy the criterion, so the reading is a decision
for the owner. A failure reads the same when its findings, with line numbers, shas and whitespace removed, name the same
criteria with the same output, in any order. A changed finding, a new commit or a passing gate starts the count again.
"""
import re

REPEATS = 2
LINE = re.compile(r"\bline \d+\b|(\.[A-Za-z]\w{0,7}):\d+\b")
SHA = re.compile(r"\b(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}\b")


def normalize(text):
    return " ".join(SHA.sub("", LINE.sub(lambda m: m.group(1) or "", str(text))).split())


def fingerprint(findings, evidence):
    """The reading of one failure: each finding with its evidence, normalized, sorted so order is not significant."""
    evidence = evidence or {}
    return tuple(sorted(normalize(f) + "\n" + normalize(evidence.get(f, "")) for f in findings))


class Streak:
    """Counts identical failures in a row. A changed fingerprint or a changed head restarts the count at one."""

    def __init__(self):
        self.fp, self.head, self.count = None, None, 0

    def failed(self, fp, head):
        self.count = self.count + 1 if fp == self.fp and head == self.head else 1
        self.fp, self.head = fp, head
        return self.count >= REPEATS

    def reset(self):
        self.fp, self.head, self.count = None, None, 0
