"""The router's dispatch envelope, in one place (#436).

The envelope is the `allow.models` of the run graph's dispatch node. The hub (the models panel's badge) and launch.py
(the start-up warning) both import this module, so the two never read different sources or apply different rules."""


def envelope_models(doc):
    """The models the dispatch node of a run graph allows, in the order it lists them; [] when it has none."""
    for n in (doc or {}).get("nodes") or []:
        if n.get("kind") == "dispatch":
            return list((n.get("allow") or {}).get("models") or [])
    return []


def envelope_blocked(chain, envelope):
    """The ids of `chain` the envelope does not allow. An alias, a `claude:` prefix and a `claude-` provider spelling name the same model.
    An empty envelope allows all: nothing was read, so nothing is compared."""
    def key(m):
        m = {"opus": "claude-opus-5-5", "sonnet": "claude-sonnet-5-5", "haiku": "claude-haiku-4-5-20251001"}.get(m.removeprefix("claude:"), m.removeprefix("claude:"))
        return m if m.startswith(("claude-sonnet", "claude-opus", "claude-haiku", "claude-fable")) else m.removeprefix("claude-")
    allowed = {key(m) for m in envelope}
    return [m for m in chain if allowed and key(m) not in allowed]
