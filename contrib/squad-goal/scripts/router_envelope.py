# modelreg: seam-clean
"""The router's dispatch envelope, in one place (#436).

The envelope is the `allow.models` of the run graph's dispatch node. The hub (the models panel's badge) and launch.py
(the start-up warning) both import this module, so the two never read different sources or apply different rules.
The key is the resolver's (SPEC 505)."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))   # the resolver sits beside this script; a run under -I has the script's directory off sys.path
import modelreg  # noqa: E402


def envelope_models(doc):
    """The models the dispatch node of a run graph allows, in the order it lists them; [] when it has none."""
    for n in (doc or {}).get("nodes") or []:
        if n.get("kind") == "dispatch":
            return list((n.get("allow") or {}).get("models") or [])
    return []


def key(m):
    """The id two spellings share: the registry id, with its context suffix when the spelling carries one.
    An unknown spelling is its own key, because the resolver passes it through."""
    ref = modelreg.normalize(m)
    return f"{ref.id}[{ref.ctx}]" if ref.known and ref.ctx else ref.id


def envelope_blocked(chain, envelope):
    """The ids of `chain` the envelope does not allow, by the resolver's key: an alias, a `claude:` prefix and a
    provider spelling name the same model. An empty envelope allows all: nothing was read, so nothing is compared."""
    allowed = {key(m) for m in envelope}
    return [m for m in chain if allowed and key(m) not in allowed]
