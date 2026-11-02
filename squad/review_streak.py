"""A review streak escalates (ADR-0151): identical findings twice stop the fix cycle, and the next fix round that
answers them uses another coder model.

The findings of a review are read by their fingerprint, `revformat.body_fingerprint`, the one reading ADR-0134 keeps
(a moved line or a new sha is not a change). Two reviews of the same finding in a row are the same finding twice; a
fix round that wrote no artifact is the finding unanswered, so it repeats the finding it left open at once (#735).

The module is pure: the engine's fix rounds and their record decide the sequence, and this module reads it.
"""
import importlib
import importlib.util
import pathlib
import sys

_REVFORMAT: dict = {}


def _revformat():
    """revformat.py, the module that reads a review's shape and its findings' fingerprint (ADR-0134). It is loaded from
    the checkout, next to review.sh, so this module and the loop read one rule."""
    if "m" not in _REVFORMAT:
        try:
            _REVFORMAT["m"] = importlib.import_module("revformat")
        except ImportError:
            path = pathlib.Path(__file__).resolve().parents[1] / "contrib" / "squad-goal" / "scripts" / "revformat.py"
            spec = importlib.util.spec_from_file_location("revformat", path)
            m = importlib.util.module_from_spec(spec)
            sys.modules.setdefault("revformat", m)
            spec.loader.exec_module(m)
            _REVFORMAT["m"] = m
    return _REVFORMAT["m"]


def fingerprint(findings):
    """The value of one round's findings, the reading ADR-0134 keeps: a moved line or a new sha is no change."""
    return _revformat().body_fingerprint((findings or "").encode())


def with_unanswered(seq, unanswered):
    """`seq` with the findings a fix round wrote no artifact for, repeated in place (#735). `unanswered[i]` is True when
    the fix round that answered round `i` ended with no artifact: the finding stands, so it is not a new one."""
    out = []
    for i, value in enumerate(seq):
        out.append(value)
        if i < len(unanswered) and unanswered[i]:
            out.append(value)
    return out


def repeated(seq):
    """True when the last findings of `seq` repeat the ones before them: the finding's second round."""
    return len(seq) > 1 and seq[-1] == seq[-2]


def changes(seq):
    """How many times identical findings twice changed the coder: once per repeated finding, and never twice for the
    same run of them (a third identical round changes no coder a second time)."""
    n, prev, run = 0, None, 0
    for value in seq:
        run = run + 1 if value == prev else 1
        prev = value
        if run == 2:
            n += 1
    return n


def at(chain, steps, first=None):
    """The coder `steps` places down `chain`, led by `first` (the child's own model). The last entry stays when the chain
    has no further one (ADR-0151 rule 2). `steps` is the ledger's `moves`: one per repeated finding (#702)."""
    chain = [first] + [m for m in (chain or []) if m and m != first] if first else list(chain or [])
    if not chain:
        return first
    return chain[min(max(int(steps or 0), 0), len(chain) - 1)]


def coder(chain, seq, first=None):
    """The coder a fix round after `seq` uses: `chain`, led by `first` (the child's own model), moved one place for
    each repeated finding. The last entry stays when the chain has no further one (ADR-0151)."""
    return at(chain, changes(seq), first)


NO_ARTIFACT = ("artifact_missing", "child_ended_early", "session_end_unknown")    # session.missing_artifact_reason; a session_error:<subtype> is one too; artifact_missing is the reason of a record written before #1066


def unanswered(outcome, reasons):
    """True when a fix round wrote no artifact: the finding it answered still stands. A `pass` wrote its artifact, and
    any other failure (a deadline, a cancel, a check, a cap) is no reading of the finding."""
    return outcome == "fail" and any(r in NO_ARTIFACT or r.startswith("session_error:") for r in reasons or ())
