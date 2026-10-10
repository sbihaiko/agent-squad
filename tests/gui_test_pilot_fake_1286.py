"""The adapter roster of the committed pad-only pilot (MesenAI #1286): which target each batch runs on.

The pilot's batches do not all live on one adapter. `launch`, `home` and `library` are its automated batches and need a
target whose Home and Library answer the checks the script asks for; every other batch is `manual` - a person runs the
cases by hand and records a verdict - so its adapter only has to launch, and its cases end `pending` for that person.

`ADAPTERS` names both halves on purpose: a batch in neither is a red e2e case, so the day the script grows a batch,
wiring it is a decision someone makes here instead of a batch that quietly runs nothing.
"""
from squad.gui_test_adapter import FakeAdapter
from tests.gui_test_pilot_fake import PilotFake


class ScenarioFake(PilotFake):
    """The Play door as the scenario's own settings profile describes it: no play history, no favorites.

    The profile is not decoration - it decides what the Home holds, and what the Home holds is what a D-pad press
    walks. `fresh` means Open a ROM alone, so a press in any direction stays on it; `PilotFake`'s fixed four tiles
    would let Up escape to Recent, and a scenario whose Home answers differently from the one it declares cannot be
    run on it. History and favorites are constructor switches, so a batch that needs them says so.
    """

    def __init__(self, out_dir, history=0, favorites=0, **rest):
        super().__init__(out_dir, favorites=favorites, **rest)
        self.history = history

    def _home_order(self):
        tiles = ["play.home.continue"] if self.history else []
        if self.favorites:
            tiles.append("play.home.favorite")
        if self.history:
            tiles.append("play.home.recent")
        return tiles + ["play.home.open-rom"]


AUTOMATED_BATCHES = ("launch", "home", "library")

MANUAL_ONLY_BATCHES = frozenset({
    "home-after-game", "favorites", "favorites-home", "game", "game-archive", "game-bios", "dialogs", "pause",
    "settings", "pause-pack-multi", "settings-menu-sounds", "controller", "controller-done", "controller-b",
    "controller-no-game", "lamps", "lamps-game", "loss", "fullscreen", "feel", "trap", "journey", "two-pads",
})

ADAPTERS = {**{batch: ScenarioFake for batch in AUTOMATED_BATCHES},
            **{batch: FakeAdapter for batch in MANUAL_ONLY_BATCHES}}
