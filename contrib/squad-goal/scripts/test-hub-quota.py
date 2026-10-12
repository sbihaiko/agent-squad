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

print("== 6. the footer line carries one token per subscription — host-free, from the pass that already runs")
# The footer's own sources, not the browser: the fixtures are the two things the hub already holds for a
# subscription — the cached probe row the cost panel renders, and the percentage the owner typed by hand.
hub.PLANS = TMP / "plans.json"
hub.PLANS.write_text(json.dumps({
    "claude": {"plan": "max20", "amount": 200, "currency": "USD", "quota": None},
    "gpt": {"plan": "plus", "amount": 20, "currency": "USD", "quota": 61, "qwin": "weekly"}}))
NOW = 1800000000.0
hub.quota = {
    "claude": {"windows": [{"label": "weekly", "pct": 42, "resets": NOW + 3 * 3600 + 20 * 60}], "asof": NOW},
    "grok": {"windows": [{"label": "weekly", "pct": 88, "resets": NOW + 86400 + 2 * 3600}], "asof": NOW},
    "gpt": {"unavailable": True, "err": "no credit line in the log", "asof": NOW}}   # gemini: no probe and nobody typed one
items = hub.foot_items(hub.foot_sources(NOW), None, NOW)
subs = [i for i in items if i["key"] == "subs"]
ck(len(subs) == 1, "one subscription fact reaches the line", [i["key"] for i in items])
ck(subs and subs[0]["text"] == "CL 42% 3h 20m · GK 88% 1d 2h · GPT 61% ? · GEM ?",
   "the four tokens in SUBS' order: acronym, quota, time to reset", subs[0]["text"] if subs else None)
ck(len(items) == 6, "the fact joins the five the line already had", [i["key"] for i in items])
ck(items and items[-1]["key"] == "subs" and all(a in items[-1]["text"] for a in ("CL", "GK", "GPT", "GEM"))
   and not any(a in " ".join(i["text"] for i in items[:-1]) for a in ("CL 42%", "GK 88%")),
   "all four tokens are one fact, so the row's own drop takes them whole and no token is cut", items[-1]["text"])
ck(set(hub.SUB_ACR) == set(hub.SUBS) and all(2 <= len(v) <= 3 for v in hub.SUB_ACR.values()),
   "one 2-3 letter short form per subscription, defined beside SUBS", hub.SUB_ACR)
# The percentage is the panel's own number, the time the page's own `rel()` shape — and neither is ever invented.
hub.quota["claude"] = {"windows": [{"label": "weekly", "pct": 6.5}], "asof": NOW}
f = {i["key"]: i["text"] for i in hub.foot_items(hub.foot_sources(NOW), None, NOW)}
ck(f["subs"].startswith("CL 6.5% ?"),
   "a probe that names no reset keeps the percentage and reads ? — the panel's fpct shape under 10", f["subs"])
ck(f["subs"].split(" · ")[-1] == "GEM ?", "nothing readable is ? alone: no digit is conjured for it", f["subs"])
hub.quota["claude"] = {"windows": [{"label": "weekly", "pct": 42, "resets": NOW + 3 * 3600 + 20 * 60}], "asof": NOW}
hub.SUB_ACR["gpt"] = "XX"
ck(hub.foot_items(hub.foot_sources(NOW), None, NOW)[-1]["text"].count("XX") == 1,
   "the acronym is read from the one map, never spelled out at the use site")
hub.SUB_ACR["gpt"] = "GPT"
asked = []
hub.QUOTA_PROBES = {k: (lambda k=k: asked.append(k)) for k in hub.SUBS}
hub.foot_refresh(NOW)
ck(not asked, "the pass the sampler already runs costs no probe", asked)
ck((hub.LOAD_STATE.get("facts") or [])[-1]["text"].startswith("CL 42% 3h 20m"),
   "so the tokens reach the page through the same facts source as the rest of the line",
   (hub.LOAD_STATE.get("facts") or [{}])[-1].get("text"))

print(f"\n{checks - len(fails)}/{checks} checks passed")
if fails:
    print("FAILED: " + "; ".join(fails)); sys.exit(1)
print("SQUAD HUB QUOTA: PASS")
