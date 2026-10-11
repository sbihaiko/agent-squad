"""The shared adapter contract suite (the GUI test hook ADR, item 6): every adapter passes it."""
import re

CONTRACT = {}


def case(fn):
    CONTRACT[fn.__name__.removeprefix("contract_")] = fn
    return fn


@case
def contract_capabilities(adapter):
    caps = adapter.capabilities()
    assert set(caps) >= {"actions", "checks", "input_families", "variants"}
    assert caps["actions"] and caps["checks"]


@case
def contract_launch(adapter):
    assert adapter.launch({}) is not None


# The args each advertised action needs to be a well-formed request (#1780): a chord names the two buttons it holds,
# the keyboard names a key, the release names the button it lets go, and a goal read names the predicate it reads
# toward. Everything else is the plain press.
PAYLOADS = {"pad.chord": {"buttons": ["Select", "Start"], "ticks": 1},
            "pad.release": {"button": "Right"},
            "key.press": {"key": "Enter", "ticks": 1},
            "nav.goal": {"goal": "ui.screen == play.home"}}


@case
def contract_inject(adapter):
    adapter.launch({})
    for name in adapter.capabilities()["actions"]:
        if name.startswith("pad.") or name in PAYLOADS:
            adapter.inject(name, PAYLOADS.get(name, {"button": "Right", "ticks": 1}))


@case
def contract_check(adapter):
    adapter.launch({})
    for name in adapter.capabilities()["checks"]:
        assert adapter.check(name, {"is": "no.such.id"}) == "unknown id"


@case
def contract_wait(adapter):
    adapter.launch({})
    assert adapter.wait(None, 1) == "met"
    assert adapter.wait("ui.screen == nowhere", 2) == "timeout"


@case
def contract_capture(adapter):
    adapter.launch({})
    shot = adapter.capture()
    assert set(shot) == {"path", "size", "sha256"} and re.fullmatch(r"[0-9a-f]{64}", shot["sha256"])
    with open(shot["path"], "rb") as f:
        assert len(f.read()) == shot["size"]


@case
def contract_teardown(adapter):
    adapter.launch({})
    assert adapter.teardown() == []


def check_contract(factory):
    for fn in CONTRACT.values():
        fn(factory())
