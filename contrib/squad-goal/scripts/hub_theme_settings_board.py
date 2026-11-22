# One hub theme (#2742). hub.py compiles this file into its own module namespace, at its place in the hub's one
# namespace, so every name the hub defined before it -- and every name a later theme defines -- is a plain global.
# --- The card limit of each board column (#2432). One resolution (squad/board_limits.py): the plugin defaults, then the
# project settings file under the profile's checkout. The Spend & limits theme reads and writes it, and the Kanban header
# shows it.
_BOARD_LIMITS = {}


def _board_limit_parts():
    """The two `squad` modules the Kanban sub-group reads, loaded by the same rule as the tailnet block."""
    if "mods" not in _BOARD_LIMITS:
        _BOARD_LIMITS["mods"] = load_squad(tailnet_roots(), ("board_limits", "project_settings"))
    return _BOARD_LIMITS["mods"]


def board_column_limits():
    """Every board column's card limit, for the Kanban header. A reader that cannot load leaves the header with no limit."""
    try:
        bl, _ = _board_limit_parts()
        return bl.limits(CHECKOUT or ".")
    except Exception as e:
        log_once(f"hub: the column limits are unavailable: {type(e).__name__}: {' '.join(str(e).split())}")
        return {}


def board_column_counts(names):
    """The cards now in each column, from the loop's board cache: no GitHub read, and a card in a finished column counts
    nowhere. A cache row the vocabulary cannot place counts in no column, so the screen never invents one."""
    counts = {n: 0 for n in names}
    for i in board_cache.get("items") or []:
        if not isinstance(i, dict): continue
        closed = str(i.get("s") or "").lower() == "closed"
        try:
            col = _columns_module.view_column(i.get("st"), closed, i.get("l"), i.get("cr"))[0]
        except Exception:
            continue
        if col in counts: counts[col] += 1
    return counts


def columns_read():
    """The Kanban sub-group's data: one row per active column with its limit, the cards now and what its limit stops, and
    the file a Save writes."""
    try:
        bl, _ = _board_limit_parts()
    except Exception as e:
        return {"columns": [], "file": "", "err": f"the settings reader did not load: {type(e).__name__}: {' '.join(str(e).split())}"}
    lim = bl.limits(CHECKOUT or ".")
    read = bool(board_cache.get("t"))
    counts = board_column_counts(bl.COLUMNS)
    return {"file": str(pathlib.Path(CHECKOUT or ".") / "docs" / "squad" / "defaults.yaml"),
            "file_label": str(pathlib.Path("docs") / "squad" / "defaults.yaml"),   # the footer names the file by its repository path (#2512)
            "read": read,
            "columns": [{"name": n, "max_cards": lim.get(n), "count": counts.get(n, 0) if read else None,
                         "stops": bl.column_stop(n)} for n in bl.COLUMNS]}


def _limit_text(raw):
    """A limit as the page types it: a whole number from 1 to 100, `none`, or an empty field, which is none. None on a
    value the rule refuses, so the caller names the column."""
    if raw is None: return None
    text = str(raw).strip().lower()
    if text in ("", "none"): return None
    if not text.isdigit(): return None
    n = int(text)
    return n if 1 <= n <= 100 else None


def columns_set(body):
    """POST /api/columns: validate, diff, then write. With `confirm` false it answers the diff and writes nothing, as the
    Spend & limits theme does. A value that breaks its rule is refused with a message and no write."""
    if not isinstance(body, dict): return {"err": "bad request"}
    try:
        bl, ps = _board_limit_parts()
    except Exception as e:
        return {"err": f"the settings reader did not load: {type(e).__name__}: {' '.join(str(e).split())}"}
    try:
        cur = bl.limits(CHECKOUT or ".")
    except Exception as e:
        return {"err": f"the limits could not be read: {e}"}
    sent = body.get("columns")
    if not isinstance(sent, dict): return {"err": "bad request"}
    new = {}
    for name, raw in sent.items():
        if name not in bl.COLUMNS: return {"err": f"no board column {name!r}"}
        value = _limit_text(raw)
        text = str(raw if raw is not None else "").strip().lower()
        if value is None and text not in ("", "none"):
            return {"err": f"{name}: the limit is an integer from 1 to 100, or none"}
        new[name] = value
    diff = [f"Kanban · {n}: {cur.get(n) if cur.get(n) is not None else 'none'} -> {v if v is not None else 'none'}"
            for n, v in new.items() if v != cur.get(n)]
    if body.get("confirm") is not True: return {"diff": diff}
    changes = [(f"columns.{n}.max_cards", "none" if v is None else str(v)) for n, v in new.items() if v != cur.get(n)]
    if changes:   # every column is staged and the file is written once: a failure leaves no partial save (#2287)
        try:
            ps.set_keys(CHECKOUT or ".", changes)
        except Exception as e:
            return {"err": f"the settings file was not written: {e}", "diff": diff}
    return {"diff": diff, "state": columns_read()}


# --- The machine slots of this machine (#2435). One read (squad/machine_slots.py): runs and gate workers, each with its
# cap, the slots in use and the holder of each slot, and the children and reviews the file sets. The hub observes the
# ceiling and writes nothing to it (ADR-0162). The two caps write the project settings file, where a project may only
# lower the plugin value.
_SLOTS = {}


def _slots_parts():
    """The two `squad` modules the Machine slots sub-group reads, loaded by the same rule as the tailnet block."""
    if "mods" not in _SLOTS:
        _SLOTS["mods"] = load_squad(tailnet_roots(), ("machine_slots", "project_settings"))
    return _SLOTS["mods"]


def _slots_error(why):
    """The read's shape with the failure named: the page states it, and no row draws a guessed number."""
    return {"file": "", "file_label": str(pathlib.Path("docs") / "squad" / "defaults.yaml"), "caps": {}, "ceiling": {},
            "runs": {"cap": 0, "in_use": 0, "holders": []},
            "gates": {"cap": 0, "in_use": 0, "holders": [], "workers": 0, "slots_per_gate": 0, "gates_at_once": 0, "gates_running": 0},
            "children": 0, "reviews": 0, "err": why}


def slots_read():
    """The Machine slots sub-group's data: the runs and gate workers of this machine, across every project, each with
    its cap, the slots in use and the holder of each slot, the gates that fit at once, and the children and reviews the
    file sets."""
    try:
        ms, _ = _slots_parts()
    except Exception as e:
        return _slots_error(f"the slots reader did not load: {type(e).__name__}: {' '.join(str(e).split())}")
    try:
        got = ms.read(CHECKOUT or ".", active_runs=STATE / "active-runs.txt")
    except Exception as e:
        return _slots_error(f"the slots could not be read: {type(e).__name__}: {' '.join(str(e).split())}")
    got["file_label"] = str(pathlib.Path("docs") / "squad" / "defaults.yaml")
    return got


SLOT_CAPS = (("runs", "Runs"), ("gates", "Gate workers"))


def _slot_cap(raw):
    """A slot cap as the page types it: a whole number from 1 to the catalog's high bound, or None."""
    text = str(raw if raw is not None else "").strip()
    return int(text) if text.isdigit() else None


def slots_set(body):
    """POST /api/slots: validate, diff, then write. With `confirm` false it answers the diff and writes nothing, as the
    Spend & limits theme does. A cap above the plugin default is refused: a project may only lower the ceiling."""
    if not isinstance(body, dict): return {"err": "bad request"}
    try:
        ms, ps = _slots_parts()
    except Exception as e:
        return {"err": f"the slots reader did not load: {type(e).__name__}: {' '.join(str(e).split())}"}
    try:
        cur, top = slots_read(), ms.plugin()
    except Exception as e:
        return {"err": f"the caps could not be read: {e}"}
    if cur.get("err"): return {"err": cur["err"]}
    new = {}
    for name, label in SLOT_CAPS:
        if name not in body: continue
        n = _slot_cap(body.get(name))
        high = ps.CATALOG[f"caps.{name}"][2]
        if n is None or not 1 <= n <= high:
            return {"err": f"{label}: the cap is an integer from 1 to {high}"}
        plugin_value = top.get(name)   # a plugin file that omits the key bounds the save at the catalog's high bound, never at no bound
        ceiling = plugin_value if isinstance(plugin_value, int) and not isinstance(plugin_value, bool) else high
        if n > ceiling:
            return {"err": f"caps.{name}: {n} is above the machine-wide ceiling {ceiling}; a project may only lower it"}
        new[name] = n
    diff = [f"Machine slots · {label}: {cur[name]['cap']} -> {new[name]}"
            for name, label in SLOT_CAPS if name in new and new[name] != cur[name]["cap"]]
    if body.get("confirm") is not True: return {"diff": diff}
    changes = [(f"caps.{name}", str(new[name])) for name, label in SLOT_CAPS if name in new and new[name] != cur[name]["cap"]]
    if changes:   # both caps are staged and the file is written once: a failure leaves no partial save (#2287)
        try:
            ps.set_keys(CHECKOUT or ".", changes)
        except Exception as e:
            return {"err": f"the settings file was not written: {e}", "diff": diff}
    return {"diff": diff, "state": slots_read()}

