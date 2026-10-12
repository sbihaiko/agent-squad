#!/usr/bin/env python3
"""Hub quota probes, offline: q_grok() must read the credit fields the grok CLI actually writes, and a probe
that finds nothing must record the reason instead of leaving yesterday's reading looking like today's.

Usage: python3 contrib/squad-goal/scripts/test-hub-quota.py
No browser, no network and no CLI call: HOME points at a throwaway tree holding fixture logs.
"""
import datetime as dt, importlib.util, json, os, pathlib, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
TMP = pathlib.Path(tempfile.mkdtemp(prefix="hub-quota-"))
HOME = TMP / "home"
(HOME / ".grok/logs").mkdir(parents=True)
# hub.py reads the profile and HOME at import time, so pin both before loading it.
os.environ.update(SG_PROFILE=os.environ.get("SG_PROFILE", "example"), SG_REPO="example/repo",
                  SG_OWNER="example", SG_PROJECT="1", SG_CHECKOUT=str(TMP),
                  SG_HUB_DIR=str(TMP / "hub"), SG_STATE=str(TMP / "state"), HOME=str(HOME))
sys.path.insert(0, str(HERE))
spec = importlib.util.spec_from_file_location("hub", HERE / "hub.py")
hub = importlib.util.module_from_spec(spec); spec.loader.exec_module(hub)

LOG = HOME / ".grok/logs/unified.jsonl"
SETTINGS = HOME / ".grok/settings_cache.json"
fails, checks = [], 0


def ck(cond, label, extra=""):
    global checks
    checks += 1
    print(("  ok   " if cond else "  FAIL ") + label + (f"  [{extra}]" if extra and not cond else ""))
    if not cond: fails.append(label)


def line(ts, msg, src="grok-shell", ctx=None):
    """One line in the shape grok 1.0.50 writes to ~/.grok/logs/unified.jsonl."""
    d = {"ts": ts, "src": src, "pid": 4242, "ver": "1.0.50", "lvl": "info", "msg": msg}
    if ctx is not None: d["ctx"] = ctx
    return json.dumps(d) + "\n"


def write_log(text):
    LOG.write_text(text)


def write_settings(tier="SuperGrok", fetched="2026-10-11T09:00:00.000000Z"):
    """The config the current grok CLI caches at ~/.grok/settings_cache.json."""
    SETTINGS.write_text(json.dumps({"payload": json.dumps(
        {"fetched_at": fetched, "grok_version": "1.0.50", "identity": "x", "origin": "https://cli-chat-proxy.grok.com/v1",
         "client": "grok-shell", "settings": {"subscription_tier_display": tier, "subscription_watch_interval_secs": 60}}),
        "signature": "sig"}))


RESET = dt.datetime(2026, 10, 14, tzinfo=dt.timezone.utc).timestamp()

print("== 1. the current log format: q_grok reads the weekly credit fields the CLI writes")
# BillingConfig as grok 1.0.50 declares it (creditUsagePercent + currentPeriod{start,end}), carried by a log
# line whose wording is not the string the old probe hard-coded.
write_settings()
write_log(line("2026-10-11T09:00:01.000Z", "billing: credits config", ctx={"config": {
    "creditUsagePercent": 42.5, "currentPeriod": {"start": "2026-10-07T00:00:00Z", "end": "2026-10-14T00:00:00Z"},
    "monthlyLimit": None, "onDemandUsed": 0, "prepaidBalance": 0, "isUnifiedBillingUser": True}}))
r = hub.q_grok()
ck(isinstance(r, dict) and r.get("windows"), "q_grok returns a reading, not None", r)
w = (r or {}).get("windows") or [{}]
ck(w[0].get("pct") == 42.5, "the weekly percentage comes through", w)
ck(w[0].get("label") == "weekly", "it is labelled weekly", w)
ck(w[0].get("resets") == RESET, "the weekly reset comes through", w)
ck((r or {}).get("plan") == "SuperGrok", "the plan the CLI caches is carried along", r)

print("== 2. newest line wins, and a debug line without the fields is skipped")
write_log(line("2026-10-11T09:00:01.000Z", "billing: credits config", ctx={"config": {
    "creditUsagePercent": 12.0, "currentPeriod": {"start": "2026-10-07T00:00:00Z", "end": "2026-10-14T00:00:00Z"}}})
    + line("2026-10-11T09:30:00.000Z", "session_end.worker_join", src="grok-pager", ctx={"outcome": "joined"})
    + line("2026-10-11T09:45:00.000Z", "billing: credits config", ctx={"config": {
        "creditUsagePercent": 61.0, "currentPeriod": {"start": "2026-10-07T00:00:00Z", "end": "2026-10-14T00:00:00Z"}}}))
r = hub.q_grok()
ck(((r or {}).get("windows") or [{}])[0].get("pct") == 61.0, "the newest credit line wins", r)

print("== 3. a log with no usable line: an unavailable reading that names the reason")
write_log("".join(line(ts, msg, src=src, ctx=ctx) for ts, msg, src, ctx in (
    ("2026-10-11T09:00:00.000Z", "session created", "grok-shell", {"sid": "01a1"}),
    ("2026-10-11T09:05:00.000Z", "shell.turn.inference_done", "grok-shell", {"ok": True}),
    ("2026-10-11T09:10:00.000Z", "session_end.worker_join", "grok-pager", {"outcome": "joined"}))))
r = hub.q_grok()
ck(isinstance(r, dict), "an empty probe still returns a reading", r)
ck(not (r or {}).get("windows"), "no windows are invented", r)
ck((r or {}).get("unavailable") is True, "the reading is marked unavailable", r)
ck(bool((r or {}).get("err")), "the reading carries why", r)

print("== 4. the state file carries the reason, so a dead probe cannot pass as a fresh reading")
hub.QUOTA_PROBES = {"grok": hub.q_grok}      # only the grok probe; claude/gpt/gemini are not called here
hub.quota["grok"] = {"windows": [{"label": "weekly", "pct": 3.0, "resets": RESET}], "asof": 1.0}   # yesterday's good read
hub.refresh_quota_once()
saved = json.loads((TMP / "hub/hub-quota.json").read_text())["grok"]
ck(saved.get("unavailable") is True, "hub-quota.json records the probe as unavailable", saved)
ck(bool(saved.get("err")), "hub-quota.json records the reason", saved)
ck(not saved.get("windows"), "the stale windows are not kept looking current", saved)

print("== 5. the panel row says so, the way the quota row already renders an age")
row = hub.quota_row("grok")
ck(isinstance(row, dict) and row.get("unavailable") is True, "the row the panel reads is unavailable", row)
ck(bool(row.get("err")), "the row carries the reason", row)
ck('chip err">unavailable' in hub.PAGE and "esc(a.err)" in hub.PAGE and "aq.unavailable" in hub.PAGE,
   "the cost panel renders the unavailable row (chip + reason, and routes it into the bar)")
hub.quota["grok"] = {"windows": [{"label": "weekly", "pct": 7.5, "resets": RESET}], "asof": 2.0}
ck(hub.quota_row("grok").get("windows")[0]["pct"] == 7.5, "a good reading still passes through unchanged")
ck(hub.quota_row("claude") is None, "an unseen provider has no row")

print(f"\n{checks - len(fails)}/{checks} checks passed")
if fails:
    print("FAILED: " + "; ".join(fails)); sys.exit(1)
print("SQUAD HUB QUOTA: PASS")
