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


@case
def contract_inject(adapter):
    adapter.launch({})
    for name in adapter.capabilities()["actions"]:
        if name.startswith("pad."):
            adapter.inject(name, {"button": "Right", "ticks": 1})


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
