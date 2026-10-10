import copy


def step(sid, **over):
    s = {
        "id": sid,
        "role": "under-test",
        "precondition": "ui.screen == play.home",
        "action": {"name": "pad.press", "args": {"button": "Right", "ticks": 4}},
        "wait": {"check": "ui.focused == play.home.open-rom", "timeout_ticks": 120},
        "check": {"name": "ui.focused", "args": {"is": "play.home.open-rom"}},
        "expect": "the Open a ROM card has focus",
        "severity": "major",
        "mode": "automated",
    }
    s.update(over)
    return s


def script(*steps, **over):
    d = {
        "format": "gui-test/1",
        "name": "tiny",
        "target": "fake",
        "requires": {"actions": ["pad.press", "nav.goal", "window.mode"],
                     "checks": ["ui.focused", "ui.screen"]},
        "variants": {"window.mode": ["windowed", "fullscreen"]},
        "fixtures": {},
        "batches": [{"id": "b1", "setup": [], "teardown": [],
                     "steps": list(steps) or [step("b1.one")]}],
    }
    d.update(over)
    return copy.deepcopy(d)
