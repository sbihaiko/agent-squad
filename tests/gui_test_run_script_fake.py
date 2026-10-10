"""A fake adapter for the run-script command's own cases (#1222): it answers a check the way mesen-gui does.

`squad.gui_test_adapter.FakeAdapter` answers a check with a bare bool; the emulator adapter answers with the observed
value too (`{"passed": ..., "observed": ...}`), which is what lets a failed step name "expected X, observed Y". This
fake speaks the emulator's shape, so the command and the supervised runner are exercised against it without a build.
"""
from pathlib import Path

from squad.gui_test_adapter import CONTROLS, SCREENS, FakeAdapter


class UnknownId(Exception):
    """A check named an id the application does not have (mesen-gui raises this; the worker reports it as an error)."""


class ShapeAdapter(FakeAdapter):
    """mesen-gui's answer shape: the observed value travels with the verdict, an unknown id raises."""

    def capabilities(self):
        return super().capabilities() | {"input_families": ["pad"]}

    def check(self, name, args):
        state = self.ui_state()
        observed = {"ui.screen": state["screen"], "ui.focused": state["focus"]}[name]
        if args["is"] not in SCREENS | CONTROLS:
            raise UnknownId(args["is"])
        return {"passed": observed == args["is"], "observed": observed}


class VariantAdapter(ShapeAdapter):
    """ShapeAdapter plus the optional `set_variant` hook, which the supervised run switches a variant through (#1243).

    The switch is what a case can read back: the fake appends the axis and value it was given next to the run's
    artifacts, so a case says which window mode the command actually asked for, per batch process.
    """

    def set_variant(self, axis, value):
        with (Path(self.out_dir) / "variant.log").open("a", encoding="utf-8") as log:
            log.write(f"{axis}={value}\n")


class OneModeAdapter(ShapeAdapter):
    """An adapter whose window axis runs one way only: a selection of that value has nothing to switch (#1243)."""

    def capabilities(self):
        return super().capabilities() | {"variants": {"window.mode": ["windowed"]}}
