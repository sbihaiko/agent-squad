# One hub theme (#2742). hub.py compiles this file into its own module namespace, at its place in the hub's one
# namespace, so every name the hub defined before it -- and every name a later theme defines -- is a plain global.
# --- who is eating the tokens. `tokens.py burners` holds every rule (the window, the threshold, `over`, the order); the
# hub runs it, caches the answer, and draws the burners it names. No script, a nonzero exit or unreadable output is no data.
TOKENS_PY = pathlib.Path(__file__).resolve().parent / "tokens.py"
BURN_HOURS = 24
BURN_TTL = 120.0      # the sampler ticks every few seconds; the burners change by the minute
BURN_MAX_CHARS = 72   # the block's own cap: the page drops whole blocks when the line is too wide, so one over-long
                      # block would take the whole line with it (#446)
_BURN = {"t": 0.0, "v": None}

def burn_read(script=None, state=None, checkout=None, hours=BURN_HOURS):
    """The parsed `tokens.py burners --json` reading, or None when there is no data."""
    script = pathlib.Path(script or TOKENS_PY)
    if not script.is_file(): return None
    try:
        r = subprocess.run([sys.executable, str(script), "burners", str(state or STATE), "--checkout", str(checkout or CHECKOUT),
                            "--hours", str(hours), "--json"], capture_output=True, text=True, timeout=20)
        if r.returncode: return None
        d = json.loads(r.stdout)
    except Exception: return None
    return d if isinstance(d, dict) else None

def burn_cached(now=None):
    now = time.time() if now is None else now
    if now - _BURN["t"] >= BURN_TTL: _BURN.update(t=now, v=burn_read())
    return _BURN["v"]

# --- Spend & limits (#926). The token guard reads its on/off switch, its daily cap and its pause factor from the profile,
# and env.sh sources that profile on every pass, so a value this section writes is read on the guard's next pass with no
# restart. The page renders; reading, validating, diffing, writing and recording live here (ADR-0137). The profile and the
# ledger are each written to a temp file and renamed, so a reader never sees half a file (ADR-0030).
SCRIPTS = pathlib.Path(__file__).resolve().parent
LIMIT_KEY = {"guard": ("SG_TOKEN_GUARD", "on"), "cap": ("SG_TOKEN_BURN_CAP", "1500000"),
             "factor": ("SG_TOKEN_PAUSE_FACTOR", "3"), "window": ("SG_TOKEN_BURN_WINDOW_H", "24")}
LIMIT_LABEL = {"cap": "token cap", "factor": "pause factor"}
LIMIT_LEDGER = "spend-limits-ledger.jsonl"
MODELS_LEDGER = "models-switch-ledger.jsonl"   # one line per model-level switch flip (SPEC 889 #920)

def profiles_dir():
    """The directory the profiles live in: SG_PROFILES_DIR when the runner exports it (env.sh does), else the seam's answer."""
    d = os.environ.get("SG_PROFILES_DIR") or PROFILES_DIR_AT_IMPORT
    if d: return pathlib.Path(d)
    try:
        r = subprocess.run(["bash", "-c", '. "$1/address.sh" >/dev/null 2>&1; sg_profiles_dir', "_", str(SCRIPTS)],
                           capture_output=True, text=True, timeout=20)
    except Exception: return None
    return pathlib.Path(r.stdout.strip()) if r.stdout.strip() else None

def profile_file():
    """The profile in force: the one file the token guard sources on every pass. None when no profile is in force, and a
    theme that cannot name the file reads nothing and writes nothing."""
    slug, d = PROFILE_NAME, profiles_dir()
    return d / f"{slug}.sh" if slug and d is not None else None

def profile_limits(path=None):
    """The four settings of that profile, as the guard reads them with the environment silent: address.sh's
    `sg_profile_setting <slug> <KEY> <default>` sources the file exactly as env.sh does. One bash call for all four."""
    path = path or profile_file()
    raw = {k: d for k, (_, d) in LIMIT_KEY.items()}
    if path is None: return raw
    script = '. "$1/address.sh" >/dev/null 2>&1; ' + "; ".join(
        f'printf "%s\\n" "$(sg_profile_setting "$2" {key} {d})"' for key, d in LIMIT_KEY.values())
    try:
        r = subprocess.run(["bash", "-c", script, "_", str(SCRIPTS), path.stem], capture_output=True, text=True, timeout=20,
                           env={**os.environ, "SG_PROFILES_DIR": str(path.parent)})
        got = r.stdout.splitlines() if r.returncode == 0 else []
    except Exception: got = []
    for (k, _), v in zip(LIMIT_KEY.items(), got):
        if v.strip(): raw[k] = v.strip()
    return raw

def _positive_int(v):
    """A daily cap: a positive whole number. A bool is not a number here, and neither is "1.5", "1_000" or "12 34"."""
    if isinstance(v, bool): return None
    try: n = int(str(v).strip())
    except (TypeError, ValueError): return None
    return n if n > 0 and str(v).strip() == str(n) else None

CAP_UNITS = {"K": 1000, "M": 1000000, "B": 1000000000, "T": 1000000000000}

def _cap_value(v):
    """A daily cap: a positive whole number, or that number with a K, M, B or T unit (6M, 1.5M, 250M, 1T). The hub counts
    in whole integers and stores the plain integer the guard reads, whatever the owner typed: a fraction of a unit is
    multiplied to the exact digit, never through a float that would round it to a different number (#2434)."""
    n = _positive_int(v)
    if n is not None: return n
    if isinstance(v, bool): return None
    m = re.fullmatch(r"(\d+)(?:\.(\d+))?\s*([KMBT])", str(v).strip(), re.IGNORECASE)
    if not m: return None
    unit = CAP_UNITS[m.group(3).upper()]
    n = int(m.group(1)) * unit
    frac = m.group(2)
    if frac:
        scaled, scale = int(frac) * unit, 10 ** len(frac)
        if scaled % scale: return None   # that fraction of the unit denotes no whole number of tokens
        n += scaled // scale
    return n if n > 0 else None

def _positive_num(v):
    """A pause factor: a positive number, whole or fractional. A bool is not one."""
    if isinstance(v, bool): return None
    try: n = float(str(v).strip())
    except (TypeError, ValueError): return None
    return n if n > 0 and n == n and n != float("inf") else None

def _guard_value(v):
    return v if v in ("on", "off") else None

def _num(n):
    return f"{n:g}"

def limits_settings(path=None):
    """The guard's settings as the theme shows and edits them: the profile's value where it is one, else the guard's default."""
    raw = profile_limits(path)
    raw["cap"] = _positive_int(raw["cap"]) or int(LIMIT_KEY["cap"][1])
    raw["factor"] = _positive_num(raw["factor"]) or float(LIMIT_KEY["factor"][1])
    raw["window"] = _positive_int(raw["window"]) or int(LIMIT_KEY["window"][1])
    raw["guard"] = "off" if str(raw["guard"]).strip().lower() in ("off", "0", "no", "false") else "on"
    return raw

def limit_line(cap, factor, window_h):
    """The hourly burn that pauses dispatch: SG_TOKEN_PAUSE_FACTOR times the cap's hourly share, as token-guard.sh computes it."""
    share = cap * 3600 // (window_h * 3600)
    return int(share * factor)

def limits_last():
    """The last change the ledger records, or None: {"at": local time, "text": the diff line}."""
    try: lines = [ln for ln in (STATE / LIMIT_LEDGER).read_text().splitlines() if ln.strip()]
    except OSError: return None
    for ln in reversed(lines):
        try: rec = json.loads(ln)
        except ValueError: continue
        if isinstance(rec, dict) and rec.get("text"): return rec
    return None

def limits_changes(limit=20):
    """The changes the ledger records, newest first, at most `limit` of them: the Tokens group's Changes list (#2510)."""
    try: lines = [ln for ln in (STATE / LIMIT_LEDGER).read_text().splitlines() if ln.strip()]
    except OSError: return []
    recs = []
    for ln in reversed(lines):
        try: rec = json.loads(ln)
        except ValueError: continue
        if isinstance(rec, dict) and rec.get("text"): recs.append(rec)
        if len(recs) >= limit: break
    return recs

def limits_read():
    """The Spend & limits theme's data: the settings the guard will read on its next pass, the burn it measures, the pause
    marker it writes and the changes recorded. Every value is a fact of one file or one marker; the page renders it."""
    path = profile_file()
    s = limits_settings(path)
    hour = burn_read(hours=1)
    window = burn_read(hours=s["window"])
    return {"profile": str(path) if path else None, "file": str(path) if path else None,
            "guard": s["guard"], "cap": s["cap"], "factor": s["factor"],
            "window_h": s["window"], "pause_line": limit_line(s["cap"], s["factor"], s["window"]),
            "hour_burn": (hour or {}).get("total"), "window_burn": (window or {}).get("total"),
            "paused": (STATE / "token-pause").exists(), "changes": limits_changes(), "last_change": limits_last()}

def limits_diff(old, new):
    """The diff a Save shows before it writes: one line per changed setting, in the order the theme reads them."""
    out = []
    if new["cap"] != old["cap"]: out.append(f"Spend & limits · token cap: {old['cap']} -> {new['cap']}")
    if new["factor"] != old["factor"]: out.append(f"Spend & limits · pause factor: {_num(old['factor'])} -> {_num(new['factor'])}")
    if new["guard"] != old["guard"]: out.append(f"token guard: {new['guard']}")
    return out

def profile_set_line(text, key, value):
    """One profile's line rewritten in place, every other line kept as it is. A profile writes a setting as
    `: "${KEY:=value}"`, as `KEY=value` or as `export KEY=value`; the last line that names the key is the one bash ends on,
    so it is the one replaced, and a profile that names none gets the line appended in the file's own `:=` form."""
    pats = [re.compile(r"(\$\{" + key + r":=)([^}]*)(\})"),
            re.compile(r"((?:^|\n)[ \t]*(?:export[ \t]+)?" + key + r"=)(\"[^\"]*\"|'[^']*'|[^\s#]*)")]
    hits = [(m.start(), m.end(), m, i) for i, p in enumerate(pats) for m in p.finditer(text)]
    if hits:
        _, _, m, i = max(hits, key=lambda h: h[0])
        return text[:m.start(2)] + value + text[m.end(2):]
    return text + ("" if not text or text.endswith("\n") else "\n") + f': "${{{key}:={value}}}"' + "\n"

def limits_write(path, changes):
    """Write the profile atomically, and only a file bash parses: a profile the guard sources is never half a file, and a
    save that would break the file is refused before it replaces the good one. Returns an error, or None."""
    try: text = path.read_text()
    except OSError: text = ""
    for key, value in changes:
        text = profile_set_line(text, key, value)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(text)
        r = subprocess.run(["bash", "-n", str(tmp)], capture_output=True, text=True, timeout=20)
        if r.returncode:
            msg = next((ln for ln in r.stderr.splitlines() if ln.strip()), "bash -n failed")
            return f"the profile would not parse: {msg}"
        os.replace(tmp, path)
    except OSError as e:
        return f"the profile could not be written: {e.strerror or e}"
    finally:
        try: tmp.unlink()
        except OSError: pass
    return None

def limits_record(diff):
    """Every change with its local time, one line per change, in the state directory's own ledger. The file is rewritten
    through a temp file and a rename, so a reader sees whole lines only."""
    path = STATE / LIMIT_LEDGER
    recs = []
    try:
        for ln in path.read_text().splitlines():
            try: recs.append(json.loads(ln))
            except ValueError: continue
    except OSError: recs = []
    at = time.strftime("%Y-%m-%d %H:%M:%S")
    recs += [{"at": at, "text": t} for t in diff]
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs))
        os.replace(tmp, path)
    except OSError: pass
    finally:
        try: tmp.unlink()
        except OSError: pass

def limits_set(body):
    """POST /api/limits: validate, diff, then write. With `confirm` false it answers the diff and writes nothing, which is
    what the theme shows before it asks. A cap that is not a positive whole number is refused with a message and no write."""
    if not isinstance(body, dict): return {"err": "bad request"}
    path = profile_file()
    if path is None: return {"err": "no profile in force: SG_PROFILE and SG_PROFILES_DIR name none"}
    cur = limits_settings(path)
    cap = _cap_value(body.get("cap", cur["cap"]))
    if cap is None: return {"err": "token cap must be a positive whole number, as 1500000, or one with a K, M, B or T "
                                     "unit, as 6M, 1.5M, 250M or 1T"}
    factor = _positive_num(body.get("factor", cur["factor"]))
    if factor is None: return {"err": "pause factor must be a positive number"}
    guard = _guard_value(body.get("guard", cur["guard"]))
    if guard is None: return {"err": "token guard is on or off"}
    new = {"cap": cap, "factor": factor, "guard": guard}
    diff = limits_diff(cur, new)
    if body.get("confirm") is not True: return {"diff": diff}
    if diff:
        changes = []
        if new["cap"] != cur["cap"]: changes.append((LIMIT_KEY["cap"][0], str(new["cap"])))
        if new["factor"] != cur["factor"]: changes.append((LIMIT_KEY["factor"][0], _num(new["factor"])))
        if new["guard"] != cur["guard"]: changes.append((LIMIT_KEY["guard"][0], new["guard"]))
        err = limits_write(path, changes)
        if err: return {"err": err}
        limits_record(diff)
    return {"diff": diff, "state": limits_read()}

