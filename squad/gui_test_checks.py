"""Objective checks of gui-test/1: `ui.*` and `emu.ram` are read through the adapter (`observe`); `fs.*` and `log.*`
are evaluated here for any adapter. The same predicate serves as a step's check and as its wait condition.

A predicate is a check {"name", "args": {"is": want, ...}} or the string form `<name> [arg] == <want>`:
`ui.screen == play.library`, `emu.ram 0x00F0 == 0x03`, `fs.exists out/a.txt == true`,
`fs.contains out/a.txt == needle`, `log.contains == needle`.
"""
from pathlib import Path

UNKNOWN = "unknown id"
RUNNER_FAMILIES = ("fs.", "log.")


def parse(cond):
    head, _, want = cond.partition(" == ")
    name, *rest = head.split()
    args = {"is": want}
    if rest:
        args["addr" if name == "emu.ram" else "path"] = rest[0]
    return name, args


def _same(want, got):
    try:
        return int(str(got), 0) == int(str(want), 0)
    except ValueError:
        pass
    return str(got).lower() == str(want).lower()


def runner_owned(name):
    return name.startswith(RUNNER_FAMILIES)


def evaluate(name, args, adapter, root=None):
    """Return {ok, expected, observed}; observed is UNKNOWN when the adapter cannot resolve the target."""
    want = args.get("is", True)
    if name == "fs.exists":
        got = (Path(root or ".") / args["path"]).exists()
        return {"ok": _same(want, got), "expected": f"{name} {args['path']} == {want}", "observed": str(got).lower()}
    if name == "fs.contains":
        f = Path(root or ".") / args["path"]
        text = f.read_text(errors="replace") if f.is_file() else None
        return {"ok": text is not None and str(want) in text, "expected": f"{args['path']} contains {want!r}",
                "observed": "missing file" if text is None else text[-80:]}
    if name == "log.contains":
        text = adapter.log_text()
        return {"ok": str(want) in text, "expected": f"log contains {want!r}", "observed": text[-80:]}
    got = adapter.observe(name, args)
    label = name + (f" {args['addr']}" if "addr" in args else "")
    return {"ok": got != UNKNOWN and _same(want, got), "expected": f"{label} == {want}", "observed": got}


def holds(cond, adapter, root=None):
    name, args = parse(cond)
    return evaluate(name, args, adapter, root)["ok"]
