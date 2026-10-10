"""Variant axes for gui-test/1 runs (window.mode first) and the local-only real-window adapter mode."""
import copy

from squad.gui_test_run import run

REQUIRED = ("accessibility", "screen-recording")


class RealWindowRefused(RuntimeError):
    """The real-window mode is local only; it is never started in CI."""


class PermissionsMissing(RealWindowRefused):
    def __init__(self, missing):
        self.missing = missing
        super().__init__("real-window mode needs macOS permission(s): " + ", ".join(missing))


def _values(tag):
    return None if tag is None else [tag] if isinstance(tag, str) else list(tag)


def run_variants(script, adapter, axis="window.mode", **kw):
    """Run the script once per declared value of `axis`; a step tagged for several values runs once per value,
    an untagged step runs in every value. Each result carries its `variant`."""
    out = []
    for value in script["variants"].get(axis, [None]):
        if value is not None and hasattr(adapter, "set_variant"):
            adapter.set_variant(axis, value)
        view = copy.deepcopy(script)
        for b in view["batches"]:
            for part in ("setup", "steps", "teardown"):
                b[part] = [s for s in b[part] if _values((s.get("variants") or {}).get(axis)) in (None, [])
                           or value in _values(s["variants"][axis])]
        out += [{**r, "variant": value} for r in run(view, adapter, **kw)]
    return out


class RealWindowAdapter:
    """Wraps an adapter so full-screen cases drive a real window (OS input + screen capture). It refuses to start in CI
    and without Accessibility and Screen Recording, before anything reaches the wrapped adapter."""

    def __init__(self, inner, env, permissions):
        self.inner, self.env, self.permissions, self.real_window = inner, env, permissions, False

    def launch(self, fixtures):
        if self.env.get("CI") or self.env.get("GITHUB_ACTIONS"):
            raise RealWindowRefused("real-window mode is local only and is not used in CI")
        missing = [p for p in REQUIRED if p not in self.permissions()]
        if missing:
            raise PermissionsMissing(missing)
        self.real_window = True
        self.inner.launch(fixtures)
        return self

    def set_variant(self, axis, value):
        if (axis, value) == ("window.mode", "fullscreen") and not self.real_window:
            self.launch({})
        if hasattr(self.inner, "set_variant"):
            self.inner.set_variant(axis, value)

    def __getattr__(self, name):
        return getattr(self.inner, name)
