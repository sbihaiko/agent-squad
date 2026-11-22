# One hub theme (#2742). hub.py compiles this file into its own module namespace, at its place in the hub's one
# namespace, so every name the hub defined before it -- and every name a later theme defines -- is a plain global.
class Access(NamedTuple):
    allowed: bool
    reason: str
    login: str      # the login the page header shows; empty off the tailnet
    tailnet: bool   # the request is addressed to the tailnet name

LOGIN_HEADER = "Tailscale-User-Login"   # the seam's name, for a hub with no seam: a request that carries it is never loopback

def route_path(path):
    """The route a request names, its query dropped (#2492)."""
    return path.split("?", 1)[0]

PROJECT_LOOP_ROUTE = re.compile(r"^/([A-Za-z0-9][A-Za-z0-9._-]*)/api/loop$")   # one project's loop write (#1599)

def route_action(method, path):
    """What a request asks for, the access seam's action (ADR-0152 rule 9): a view, or a control write.
    The seam decides on the action; a method only names which routes can write."""
    path = route_path(path)
    if method == "GET": return "view"
    return "control"

def access(h, action):
    """The one rule for every route: the loopback Host as own_host has always answered it, a tailnet request through the seam.
    A request that carries the tailnet login header is a tailnet request whatever its Host: on a loopback Host it reads, and
    it never writes, so a `tailscale serve` that hands the hub a loopback Host never earns a loopback write (ADR-0152 rule 9)."""
    seam = tailnet_seam()
    login = h.headers.get(seam.LOGIN_HEADER if seam else LOGIN_HEADER) or ""
    if own_host(h) and not login: return Access(True, "", "", False)
    if seam is None: return Access(False, "no tailnet access seam", "", bool(login))
    host = h.headers.get("Host") or ""
    tn = tailnet_block()
    v = seam.decide(host=host, origin=h.headers.get("Origin"), action=action, login=login, port=h.server.server_port,
                    tailnet_host=tn.get("host") or "", allowed_logins=tn.get("allow") or [])
    return Access(v.allowed, v.reason, login if v.allowed and not own_host(h) else "", bool(login) or (bool(tn.get("host")) and host == tn["host"]))

DASH_PREFIXES = ("/dash/", "//")
DASH_PATH = re.compile(r"/dash/([\w-]+)(/[\w./-]*)?")   # a run id, then plain names only: no escape, host or port
DASH_TIMEOUT_SECS = 60.0
DASH_REPLY_HEADERS = ("content-type", "content-length", "cache-control", "x-content-type-options", "referrer-policy")
def own_origin(h):
    """A loopback write's one Origin rule: the hub's own exact origin, on either loopback name and its own port (#824)."""
    org = h.headers.get("Origin") or ""
    return org in (f"http://127.0.0.1:{h.server.server_port}", f"http://localhost:{h.server.server_port}")

def dash_port(run_id):
    """The port the run list names for this run id, or None: the one address a forwarded request can reach."""
    for r in run_list():
        if r.get("id") == run_id and str(r.get("port") or "").isdigit(): return int(r["port"])
    return None

def dash_target(path):
    """(run id, path on that dashboard) for a /dash/ path, or None when it names anything but a run's own path."""
    m = DASH_PATH.fullmatch(path.split("?", 1)[0])
    if not m: return None
    rest = m[2] or "/"
    if ".." in rest.split("/"): return None
    return m[1], rest

def request_target(h):
    """The target as the client sent it: the handler turns a leading // into /, which would make //host read as a page."""
    parts = h.requestline.split()
    return parts[1] if len(parts) > 1 and parts[1].startswith("//") else h.path

