# One hub theme (#2742). hub.py compiles this file into its own module namespace, at its place in the hub's one
# namespace, so every name the hub defined before it -- and every name a later theme defines -- is a plain global.
# --- The project's Slack channel on Now (SPEC 2498, #2500). The hub resolves the address for one profile with reads
# alone: the workspace domain comes from the Slack connection itself, and the channel comes from the always-on daemon's
# own map, which it filled when it joined the channel once. So the hub calls `auth.test` only: it lists no channel,
# joins none and writes nothing. A profile may answer from its own connection and channel: a profile-qualified Keychain
# entry and a profile-qualified channel entry win over the shared ones, and the kept answer is keyed by profile, so a
# profile switch never serves another project's link. A missing connection, channel or domain answers the unavailable
# state, which the page draws with no anchor.
SLACK_TOKEN_SERVICE = "agent-squad-voice-slack-bot-token"   # contrib/voice/keychain.py BOT_TOKEN: the shared workspace connection
SLACK_WORKSPACE = re.compile(r"^https://([A-Za-z0-9][\w.-]*?[A-Za-z0-9])\.slack\.com/?$")
SLACK_LINK_SECS = 300.0
KEYCHAIN = sys.platform == "darwin"   # the one reader is the macOS Keychain: another host has no connection to read
_SLACK_LINK = {}   # (profile, repository) -> {"at", "answer"}: one window per profile, never one window for the machine
_SLACK_LINK_LOCK = threading.Lock()

def profile_of():
    """The profile a request-time read answers for: the name this hub was loaded under, else the environment's.

    hubmux restores the shell's SG_PROFILE after the import, so the pinned name wins whenever the hub was loaded with one
    (#1073). A hub loaded with no profile still follows the environment, which is how a hand-run read names its profile."""
    return PROFILE_NAME or os.environ.get("SG_PROFILE", "")

def voice_state_dir():
    """The always-on daemon's state directory: VOICE_STATE when the caller exports it, else its default."""
    return pathlib.Path(os.environ.get("VOICE_STATE") or (pathlib.Path.home() / ".local" / "state" / "agent-squad" / "voiced"))

def slack_token(profile=None):
    """The workspace bot token, read from the macOS Keychain. A profile's own entry wins over the shared voice entry, so
    two profiles may answer from two workspaces. The value never leaves this reader to a print or a log."""
    p = profile_of() if profile is None else profile
    services = [f"{SLACK_TOKEN_SERVICE}:{p}", SLACK_TOKEN_SERVICE] if p else [SLACK_TOKEN_SERVICE]
    for service in services:
        try:
            r = subprocess.run(["security", "find-generic-password", "-s", service, "-w"],
                               capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            return None
        if r.returncode == 0 and r.stdout.strip(): return r.stdout.strip()
    return None

def slack_api(token, method):
    """One Slack read. The hub calls `auth.test` alone: it lists no channel, joins none and writes nothing."""
    req = urllib.request.Request("https://slack.com/api/" + method, headers={"Authorization": "Bearer " + token})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode() or "{}")

def slack_workspace(profile=None):
    """(domain, error): the workspace domain from the Slack connection, never a constant in this repository."""
    if not KEYCHAIN: return None, "no keychain on this host"
    token = slack_token(profile)
    if not token: return None, "no connection"
    try: d = slack_api(token, "auth.test")
    except Exception: return None, "the connection did not answer"
    if not isinstance(d, dict) or not d.get("ok"): return None, "the connection refused"
    m = SLACK_WORKSPACE.match(str(d.get("url") or ""))
    if not m: return None, "no workspace domain"
    return m.group(1), None

def slack_channel(repo=None, profile=None):
    """(channel, error): the project's channel from the daemon's map, a read that never joins a channel.

    The daemon keys the map by repository. A profile-qualified entry (`<profile>:<repo>`) wins when the map holds one, so
    two profiles that name one repository still resolve their own channel."""
    repo = repo or REPO
    profile = profile_of() if profile is None else profile
    try: d = json.loads((voice_state_dir() / "channels.json").read_text())
    except (OSError, ValueError): return None, "no channel for " + str(repo)
    ch = None
    if isinstance(d, dict):
        if profile: ch = d.get(f"{profile}:{repo}")
        ch = ch or d.get(repo)
    if not isinstance(ch, dict) or not ch.get("id"): return None, "no channel for " + str(repo)
    return ch, None

def slack_link(now=None, refresh=False, profile=None, repo=None):
    """The project channel's archive address on the workspace domain: {"state", "url", "channel", "text"}.

    The answer is cached for a window per (profile, repository); `refresh` resolves again. A missing connection, channel
    or domain answers the unavailable state with no address, and the page then draws no anchor (SPEC 2498, #2500)."""
    profile = profile_of() if profile is None else profile
    repo = repo or REPO
    key = (profile, repo)
    now = time.monotonic() if now is None else now
    with _SLACK_LINK_LOCK:
        kept = _SLACK_LINK.get(key) or {}
        at = kept.get("at")
        if not refresh and at is not None and now - at < SLACK_LINK_SECS: return kept["answer"]
        domain, why = slack_workspace(profile)
        ch = None
        if domain is not None: ch, why = slack_channel(repo, profile)
        if domain is not None and ch is not None:
            answer = {"state": "ok", "ok": True, "url": f"https://{domain}.slack.com/archives/{ch['id']}",
                      "channel": "#" + str(ch.get("name") or ch["id"]), "text": "Answer in Slack"}
        else:
            answer = {"state": "unavailable", "ok": False, "url": None, "channel": None,
                      "text": "Slack link unavailable: " + str(why) + "."}
        _SLACK_LINK[key] = {"at": now, "answer": answer}
        return answer

def _tok(n):
    n = int(n)
    return f"{n/1e6:.1f}M".replace(".0M", "M") if n >= 1e6 else f"{n/1e3:.0f}k" if n >= 1e3 else str(n)

def _burn_row(r):
    """One burner as the line reads it: its consumer's name, shortened to the two ends that tell two burners apart, then
    its tokens. A run's consumer is `run:<run id>:<node>` and the run id is `run-YYYYMMDD-HHMMSS-xxxx`, so the id's own
    tail and the node are what a reader needs: `run:run-20261015-191451-9f6b30:route.router.1` reads `…9f6b30:route.router.1`.
    Any other name is kept whole."""
    parts = str(r["consumer"]).split(":")
    if len(parts) > 2 and parts[1].startswith("run-"): parts = ["…" + parts[1].rsplit("-", 1)[-1], *parts[2:]]
    return ":".join(parts) + " " + _tok(r["tokens"])

BURN_SHOWN = 3        # how many consumers the block's tooltip names: the worst, so the hover stays one readable line

def _burn_rows(b):
    """The consumers the script named, in the script's own order: the only rows the block and its tooltip draw from.
    `over` decides, never the totals: the script is the one that compares."""
    if not isinstance(b, dict) or b.get("over") is not True: return []
    return [x for x in (b.get("burners") or []) if isinstance(x, dict) and x.get("consumer") and isinstance(x.get("tokens"), (int, float))]

def _burn_window(b):
    """`(total tokens, hours, threshold or None)` from one reading: the two numbers the block states and the one it names."""
    rows = _burn_rows(b)
    hours = b.get("window_hours") if isinstance(b, dict) and isinstance(b.get("window_hours"), (int, float)) else BURN_HOURS
    thr = b.get("threshold") if isinstance(b, dict) else None
    return sum(x["tokens"] for x in rows), hours, (thr if isinstance(thr, (int, float)) else None)

def _burn_fact(b):
    """`tokens 62.9M/24h · 79 over 5M` when the script says `over`; empty otherwise. The window, the comparison and the
    order are the script's, never this line's.
    Two numbers and no ids: what the window cost, and how many calls crossed the threshold. What the block named before --
    `…9f6b30:route.router.1 16.9M +78` -- was a machine id nobody reads across the room and a third number the reader had
    to hold while reading it; the owner read the block as soup (2026-10-16). The names are `_burn_tip`'s now, so the fact
    is moved to the hover and never lost.
    A reading the hub cannot compare against a threshold still states both numbers it has, and names the cap as the word it
    can: the block's own colour already says the reading is high. The text is cut at BURN_MAX_CHARS -- a block that never
    ends would empty the line it is the first fact of (#446)."""
    rows = _burn_rows(b)
    if not rows: return ""
    total, hours, thr = _burn_window(b)
    over = f"over {_tok(thr)}" if thr is not None else "over the cap"
    floor = " · floor" if b.get("floor") else ""   # no count came from some calls: the number is a floor, not a measure
    return f"tokens {_tok(total)}/{hours}h · {len(rows)} {over}{floor}"[:BURN_MAX_CHARS]

def _burn_tip(b):
    """What the block no longer says out loud, on hover: how many calls crossed the threshold in the window, the worst
    BURN_SHOWN of them by the script's own name for each, and the count of the rest. None when the block draws nothing."""
    rows = _burn_rows(b)
    if not rows: return None
    _, hours, thr = _burn_window(b)
    lead = f"{len(rows)} calls, each over {_tok(thr)}" if thr is not None else f"{len(rows)} calls, each over the cap"
    names, rest = [_burn_row(x) for x in rows[:BURN_SHOWN]], len(rows) - BURN_SHOWN
    return lead + f" in {hours}h · " + ", ".join(names) + (f" +{rest} more" if rest > 0 else "") \
        + (" · floor, not a measure: some calls carried no token count" if b.get("floor") else "")

def foot_items(src, now=None):
    """The facts, in the order the line reads them, each carrying its palette colour and the parts it paints: a
    subscription token carries the tone its own consumption earns, so the block is never painted as one value (#1843).
    The loop's own health and its last progress were dropped from the line by the owner: the line is a machine reading,
    and both said as much about the loop's mood as about the machine. The worktree count and the occupancy pair left it the
    same way, and each is drawn one screen away — the WORKERS header and the left pane's fourth accordion (#1797, #455):
    `foot_sources` still reads the worktree count for that accordion, and answers this line with what is left."""
    now = time.time() if now is None else now
    src = src or {}
    land = _landing_parts(src.get("landing"), now)
    gate = _gate_parts(src.get("gate"), now)
    queue = _queue_parts(src.get("queue")) if "queue" in src else []   # no source read, no block: a queue read and found empty draws `queue empty`
    nightly = _nightly_parts(src.get("nightly"), now)
    items = [_fact("burn", _burn_fact(src.get("burn")), bad=True, tip=_burn_tip(src.get("burn"))),
             _fact("landing", FOOT_SEP.join(p["text"] for p in land), parts=land),
             _fact("gate", FOOT_SEP.join(p["text"] for p in gate), bad=any(p["tone"] == "err" for p in gate),
                   parts=gate or None, tip=(gate[0].get("tip") if gate else None)),
             _fact("queue", FOOT_SEP.join(p["text"] for p in queue), parts=queue or None,
                   tip=(queue[0].get("tip") if queue else None)),
             _fact("nightly", FOOT_SEP.join(p["text"] for p in nightly), bad=any(p["tone"] == "err" for p in nightly),
                   parts=nightly or None, tip=(nightly[0].get("tip") if nightly else None),
                   detail=(nightly[0].get("detail") if nightly else None)),
             _fact("quota", _quota_fact(src.get("quota"), now)),
             _fact("cycle", "cycle off" if src.get("loop") is False and src.get("cycle") else _cycle_fact(src.get("cycle"), now)),   # a stopped loop has no next cycle
             _fact("subs", _subs_fact(src.get("subs"), now), parts=_subs_parts(src.get("subs"), now))]
    if "loop" in src:   # the loop state ends the line (the owner's ask, #554): one word read across the room, so it survives a narrow window
        on = bool(src["loop"])
        items.append(_fact("loop", "Loop:ON" if on else "Loop:OFF", bad=not on, parts=[{"text": "Loop:ON" if on else "Loop:OFF", "tone": "ok" if on else "err"}]))
    return [i for i in items if i["text"]]   # a block with nothing to say draws no lone `·`

def foot_line(items):
    """The facts as the line reads them, one `·` between each: the page appends this after the three load cells."""
    return FOOT_SEP.join(i["text"] for i in (items or []))

# The sources, each one the loop already refreshes: `health.json` plus the `alerts.log` line that armed each cause, the
# loop's own markers and `cycle.log` (a verdict, a land, a dispatch, a board move), the caps graph, the pair `health.sh`
# writes, and the markers the review chain skips models over. Reading them is a few small files in the sampler thread
# that already runs: no source here costs a query, a `ps` or a GitHub call.

def _read(p):
    try: return pathlib.Path(p).read_text(errors="ignore")
    except OSError: return ""

def _json(p):
    try: return json.loads(pathlib.Path(p).read_text())
    except Exception: return None

def _last_landing():
    """The newest landed branch the board pass has read: `{"issue", "ts"}`, naming the issue it carries, or None.

    Delivery is a work branch landed in the landing branch (#358), and the board pass already holds the landing ledger of
    every configured repository with its landing time. A row the ledger does not mark as landed is not a landing, and a
    row whose time the hub cannot read is skipped rather than counted as now.
    """
    return _last_landing_of(board_cache)

def _last_landing_of(cache):
    """`_last_landing` over any project's board cache: one project never reports another's landing (#1599)."""
    best = None
    for p in (cache or {}).get("landed") or []:
        if not p.get("m"): continue
        try: ts = dt.datetime.fromisoformat(str(p.get("c") or "").replace("Z", "+00:00")).timestamp()
        except ValueError: continue
        if best is None or ts > best["ts"]: best = {"issue": p.get("n"), "ts": ts}
    return best

def _gate_status():
    """The one line the gate wrapper leaves in $SG_STATE/gate-status, `<epoch> TAB <kind> TAB <pass|fail> TAB <sha>`, and for a
    gate that ran the test battery a fifth field, its selection (`selected N files` or `all: <reason>`), and a sixth, the
    scope it names (`delta` or `full`): the latest gate, its result, the sha it ran at, that selection and its scope. None
    when the file is absent or is not that line."""
    f = _read(STATE / "gate-status").rstrip("\n").split("\t")
    if len(f) not in (4, 5, 6) or not f[0].isdigit() or not all(f[1:4]) or (len(f) >= 5 and not f[4]): return None
    if len(f) == 6 and f[5] not in ("delta", "full"): return None
    line = {"kind": f[1], "result": f[2], "sha": f[3], "ts": float(f[0])}
    if len(f) >= 5: line["selection"] = f[4]
    if len(f) == 6: line["scope"] = f[5]
    took, avg = _gate_times(f[1])
    if took is not None: line["took"], line["avg"] = took, avg
    return line

def _gate_times(kind, n=20):
    """How long the last gate of this kind took and the mean of the last `n`, in seconds, from $SG_STATE/gate-history
    (`<finish> TAB <kind> TAB <result> TAB <sha> TAB <start>`). A line whose start equals its finish reused a verdict and
    ran nothing, so it counts in neither. (None, None) when no gate of the kind ran."""
    runs = []
    for ln in _read(STATE / "gate-history").splitlines():
        f = ln.split("\t")
        if len(f) >= 5 and f[1] == kind and f[0].isdigit() and f[4].isdigit() and int(f[0]) > int(f[4]):
            runs.append(int(f[0]) - int(f[4]))
    if not runs: return None, None
    last = runs[-n:]
    return runs[-1], round(sum(last) / len(last))

def _nightly_status():
    """The one result line the nightly full suite leaves in $SG_STATE/nightly-result, `<epoch> TAB
    <pass|fail|bounded> TAB <sha> TAB <parallel> TAB <serial> TAB <ids>` (nightly.sh, #704, #1183): the last full run in both
    modes, its result, the sha it ran at, each mode's own result and the failing test ids. None when the file is absent or
    is not that line.

    A mode is `pass`, `fail`, `fail:signal-<n>` for a red leg a signal also stopped, or `bounded:<signal|exit>-<n>` for a
    leg that ended without naming a failing test (#1183): a red stays red and holds delivery whatever also stopped it, and
    a suite a signal killed measured nothing, so the record says bounded and the tip stays unproven, never red (ADR-0164).

    `<ids>` is ONE JSON array of strings, `[]` when none: a pytest node id may itself carry a space (a parametrized case
    id), so the ids are never read as a whitespace-separated list, which would cut such an id in two. A bounded leg names
    no failing test, so a bounded record that carries one is no reading.

    A record whose modes are outside the three, whose ids are not that array, or whose result contradicts its modes — a
    `pass` over a red mode, a `bounded` over two finished legs, or a `fail` with no red leg — is not that line either: a
    record the hub cannot trust is no reading, and it never draws a pass. The record is one line, and it is read whole: a
    file with anything after its one line is no reading either, because a first line alone never proves the file whole.
    `gh_dispatch_hold` drops the same lines, so the hub and the hold never disagree about which record they are looking at
    (ADR-0153)."""
    text = _read(STATE / "nightly-result")
    if text.endswith("\n"): text = text[:-1]   # the one line may end in a newline, and nothing else follows it
    if "\n" in text: return None
    f = text.split("\t")
    if len(f) != 6 or not f[0].isdigit() or f[1] not in NIGHTLY_MODES + ("bounded",) or not f[2]: return None
    if not _nightly_mode(f[3]) or not _nightly_mode(f[4]): return None
    if f[1] != _nightly_fold(f[3], f[4]): return None
    try: ids = json.loads(f[5])
    except (TypeError, ValueError): return None
    if not isinstance(ids, list) or not all(isinstance(x, str) for x in ids): return None
    if f[1] == "bounded" and ids: return None
    out = {"result": f[1], "sha": f[2], "parallel": f[3], "serial": f[4], "ids": ids, "ts": float(f[0])}
    if (STATE / "nightly-verdict").exists(): out["verdict"] = _nightly_verdict()
    return out

def _nightly_verdict():
    """The one verdict nightly.sh writes in $SG_STATE/nightly-verdict (#1975, ADR-0153 rule 1d): `<epoch> TAB
    <pass|fail|unproven> TAB <sha> TAB <local> TAB <github> TAB <github sha>`. None when it is not that one line."""
    text = _read(STATE / "nightly-verdict")
    if text.endswith("\n"): text = text[:-1]
    f = text.split("\t")
    if "\n" in text or len(f) != 6 or not f[0].isdigit() or f[1] not in ("pass", "fail", "unproven") or not f[2]: return None
    return {"result": f[1], "sha": f[2], "local": f[3], "github": f[4], "github_sha": f[5]}

