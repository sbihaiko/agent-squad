# One hub theme (#2742). hub.py compiles this file into its own module namespace, at its place in the hub's one
# namespace, so every name the hub defined before it -- and every name a later theme defines -- is a plain global.
#
# --- Settings > Personas, the read route (SPEC 3009, #3010). The route reads one seat for each roster project and the
# profile's local client map, and the persona rules module merges them (ADR-0137, ADR-0177). The page draws what the
# route answers. Nothing here is editable yet.

def personas_module():
    """The one home of the persona rules: the hub holds no copy of a rule."""
    roots = squad_roots(SQUAD_ROOT, os.environ.get("SG_CHECKOUT"), CHECKOUT, __file__)
    (PS,) = load_squad(roots, ("personas",))
    return PS


def personas_overview(seats=None, clients=None):
    """The groups the Personas theme draws: one group for each client of the map, then one for each project no client
    names, each with one row for each persona id its projects declare. A caller may hand the seats and the client map
    in; the route reads them from the roster and the profile's map.

    A roster read that fails answers its reason and no group, so a broken profile read never draws an empty roster as a
    roster with no persona. A client map that fails to read names no client, and the projects wait in their own
    groups."""
    errs = []
    if seats is None:
        seats, roster_err = _slack_roster_checked(0.0)
        if roster_err:
            return {"err": roster_err, "groups": []}
    if clients is None:
        clients, map_err = _clients_map_read()
        if map_err:
            errs.append(map_err)
    # The route is unauthenticated: the page gets a stable message, and the exception with its paths stays in the log.
    try:
        PS = personas_module()
    except Exception as e:
        print(f"hub: the persona module did not resolve: {type(e).__name__}: {e}", file=sys.stderr)
        return {"err": "the persona rules are unavailable", "groups": []}
    try:
        groups = PS.groups(seats, clients)
    except Exception as e:
        print(f"hub: the personas could not be merged: {type(e).__name__}: {e}", file=sys.stderr)
        return {"err": "the personas could not be merged", "groups": []}
    return {"err": "; ".join(errs) or None, "groups": groups}
