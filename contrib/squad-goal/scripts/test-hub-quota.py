#!/usr/bin/env python3
"""Hub quota probes, offline: q_grok() must read the credit fields the grok CLI actually writes, and a probe
that finds nothing must record the reason instead of leaving yesterday's reading looking like today's.

Usage: python3 contrib/squad-goal/scripts/test-hub-quota.py
No browser, no network and no CLI call: HOME points at a throwaway tree holding fixture logs.
"""
import datetime as dt, importlib.util, json, os, pathlib, sys, tempfile, time

HERE = pathlib.Path(__file__).resolve().parent
TMP = pathlib.Path(tempfile.mkdtemp(prefix="hub-quota-"))
HOME = TMP / "home"
(HOME / ".grok/logs").mkdir(parents=True)
# hub.py reads the profile and HOME at import time, so pin both before loading it.
os.environ.update(SG_PROFILE=os.environ.get("SG_PROFILE", "example"), SG_REPO="example/repo",
                  SG_OWNER="example", SG_PROJECT="1", SG_CHECKOUT=str(TMP),
                  SG_HUB_DIR=str(TMP / "hub"), SG_SHARED=str(TMP / "hub"), SG_STATE=str(TMP / "state"), HOME=str(HOME))
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
(TMP / "hub/hub-grok.json").unlink(missing_ok=True)   # no reading ever kept
write_log("".join(line(ts, msg, src=src, ctx=ctx) for ts, msg, src, ctx in (
    ("2026-10-11T09:00:00.000Z", "session created", "grok-shell", {"sid": "01a1"}),
    ("2026-10-11T09:05:00.000Z", "shell.turn.inference_done", "grok-shell", {"ok": True}),
    ("2026-10-11T09:10:00.000Z", "session_end.worker_join", "grok-pager", {"outcome": "joined"}))))
r = hub.q_grok()
ck(isinstance(r, dict), "an empty probe still returns a reading", r)
ck(not (r or {}).get("windows"), "no windows are invented", r)
ck((r or {}).get("unavailable") is True, "the reading is marked unavailable", r)
ck(bool((r or {}).get("err")), "the reading carries why", r)
ck("2026-10-11T09:00:00" in (r or {}).get("err", "") and "open the grok TUI once" in (r or {}).get("err", ""),
   "the reason names the log's first entry and the one step that writes a line", r)
ck("64 MB" not in (r or {}).get("err", ""), "the reason never names a scan size the log does not have", r)
write_log("")
r = hub.q_grok()
ck("no credit line" in (r or {}).get("err", "") and "open the grok TUI once" in (r or {}).get("err", ""),
   "an empty log reads as no credit line, with the same step", r)

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
hub.quota["grok"] = {"windows": [{"label": "weekly", "pct": 7.5, "resets": RESET}], "asof": time.time()}
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
items = hub.foot_items(hub.foot_sources(NOW), NOW)
subs = [i for i in items if i["key"] == "subs"]
ck(len(subs) == 1, "one subscription fact reaches the line", [i["key"] for i in items])
ck(subs and subs[0]["text"] == "CL 42% 3.3h · GK 88% 1.1d · GPT 61% ? · GEM ?",
   "every token carries its reset in one compact rung, green ones too", subs[0]["text"] if subs else None)
ck(subs and subs[0]["parts"][0].get("tip") == "resets in 3h 20m · read 0s ago",
   "the hidden reset span is the token tooltip", subs[0]["parts"][0] if subs else None)
# Every token draws the compact span, whatever its tone, and an unreadable reset always stays visible. A reset only one
# minute away reads in its own rung; quota markers keep their absolute reset time.
hub.quota["claude"] = {"windows": [{"label": "weekly", "pct": hub.FOOT_WARN_PCT, "resets": NOW + 3 * 3600 + 20 * 60}], "asof": NOW}
boundary = [i for i in hub.foot_items(hub.foot_sources(NOW), NOW) if i["key"] == "subs"][0]
ck(boundary["parts"][0]["text"] == "CL 80% 3.3h", "a token at the warn boundary keeps the same rung", boundary["parts"][0])
hub.quota["claude"] = {"windows": [{"label": "weekly", "pct": 42, "resets": NOW + 60}], "asof": NOW}
close = [i for i in hub.foot_items(hub.foot_sources(NOW), NOW) if i["key"] == "subs"][0]
ck(close["parts"][0]["text"] == "CL 42% 1m" and close["parts"][0].get("tip") == "resets in 1m · read 0s ago",
   "a reset minutes away reads in the minute rung, its exact span in the tooltip", close["parts"][0])
ck(hub._quota_fact({"codex": NOW + 60, "agy": None}, NOW, subs=()) == "codex reset 05:01 · agy ok",
   "the quota block keeps its reset time", hub._quota_fact({"codex": NOW + 60, "agy": None}, NOW, subs=()))
hub.quota["claude"] = {"windows": [{"label": "weekly", "pct": 42, "resets": NOW + 3 * 3600 + 20 * 60}], "asof": NOW}
ck([i["key"] for i in items] == ["landing", "queue", "subs", "loop"], "the fact joins the landing the line already has, the empty queue reads beside it (#1173), and the loop state ends the line (#554)", [i["key"] for i in items])
sub = next((i for i in items if i["key"] == "subs"), {"text": ""})
ck(all(a in sub["text"] for a in ("CL", "GK", "GPT", "GEM"))
   and not any(a in " ".join(i["text"] for i in items if i is not sub) for a in ("CL 42%", "GK 88%")),
   "all four tokens are one fact, so the row's own drop takes them whole and no token is cut", sub["text"])
ck(set(hub.SUB_ACR) == set(hub.SUBS) and all(2 <= len(v) <= 3 for v in hub.SUB_ACR.values()),
   "one 2-3 letter short form per subscription, defined beside SUBS", hub.SUB_ACR)
# The percentage is the panel's own number, the time the page's own `rel()` shape — and neither is ever invented.
hub.quota["claude"] = {"windows": [{"label": "weekly", "pct": 6.5}], "asof": NOW}
f = {i["key"]: i["text"] for i in hub.foot_items(hub.foot_sources(NOW), NOW)}
ck(f["subs"].startswith("CL 6.5% ?"),
   "a probe that names no reset keeps the percentage and reads ? — the panel's fpct shape under 10", f["subs"])
ck(f["subs"].split(" · ")[-1] == "GEM ?", "nothing readable is ? alone: no digit is conjured for it", f["subs"])
hub.quota["claude"] = {"windows": [{"label": "weekly", "pct": 42, "resets": NOW + 3 * 3600 + 20 * 60}], "asof": NOW}
hub.SUB_ACR["gpt"] = "XX"
ck({i["key"]: i["text"] for i in hub.foot_items(hub.foot_sources(NOW), NOW)}["subs"].count("XX") == 1,
   "the acronym is read from the one map, never spelled out at the use site")
hub.SUB_ACR["gpt"] = "GPT"
asked = []
hub.QUOTA_PROBES = {k: (lambda k=k: asked.append(k)) for k in hub.SUBS}
hub.foot_refresh(NOW)
ck(not asked, "the pass the sampler already runs costs no probe", asked)
subs_fact = next((i for i in hub.LOAD_STATE.get("facts") or [] if i["key"] == "subs"), {})
ck(subs_fact.get("text", "").startswith("CL 42%"),
   "so the tokens reach the page through the same facts source as the rest of the line", subs_fact.get("text"))

print("== 7. a kept grok reading survives a log rotation, shows its age, and goes stale at its period end (#553)")
KEPT = TMP / "hub/hub-grok.json"
KEPT.unlink(missing_ok=True)
REAL = ('{"ts":"2026-10-18T06:37:37.069Z","src":"shell","pid":1,"ver":"1.0.62","lvl":"info","msg":"billing: fetched credits config",'
        '"ctx":{"config":{"creditUsagePercent":73.0,"currentPeriod":{"type":"USAGE_PERIOD_TYPE_WEEKLY",'
        '"start":"2026-10-15T18:51:01.060398+00:00","end":"2026-10-22T18:51:01.060398+00:00"}},"subscriptionTier":"SuperGrok"}}\n')
ASOF = dt.datetime(2026, 10, 18, 6, 37, 37, 69000, tzinfo=dt.timezone.utc).timestamp()
END = dt.datetime(2026, 10, 22, 18, 51, 1, 60398, tzinfo=dt.timezone.utc).timestamp()
write_log(line("2026-10-18T06:00:00.000Z", "session created", ctx={"sid": "01a1"}) + REAL)
r = hub.q_grok()
ck(((r or {}).get("windows") or [{}])[0].get("pct") == 73.0, "the real credit line reads 73%", r)
kept = json.loads(KEPT.read_text()) if KEPT.exists() else {}
ck(kept.get("pct") == 73.0 and kept.get("asof") == ASOF, "the reading is kept in HUBDIR with its asof", kept)
ck(not list((TMP / "hub").glob("*.tmp")), "the kept file is written through a tmp file and a rename")
write_log(line("2026-10-18T11:00:00.000Z", "session created", ctx={"sid": "01a2"}))   # the log rotated
r = hub.q_grok()
w = ((r or {}).get("windows") or [{}])[0]
ck(w.get("pct") == 73.0 and w.get("resets") == END and (r or {}).get("asof") == ASOF,
   "after a rotation the kept reading comes back, with its own asof and reset", r)
ck(not (r or {}).get("unavailable"), "a kept reading is not unavailable", r)
hub.quota = {"grok": dict(r, asof=ASOF)}
LATER = ASOF + 5 * 3600   # past QUOTA_MAX_AGE, before the period end
row = hub.quota_row("grok", LATER)
ck(row and (row.get("windows") or [{}])[0].get("pct") == 73.0, "the panel row shows the kept reading before the period end", row)
ck(row and row.get("asof") == ASOF, "the panel row carries the reading's age", row)
gk = [p for p in [i for i in hub.foot_items(hub.foot_sources(LATER), LATER) if i["key"] == "subs"][0]["parts"] if p["text"].startswith("GK")][0]
ck(gk["text"] == "GK 73% 4.3d" and "resets in 4d 7h" in (gk.get("tip") or ""), "the footer token reads the same value as the panel", gk)
ck("read 5h ago" in (gk.get("tip") or ""), "the footer token carries the same age as the panel", gk)
row = hub.quota_row("grok", END + 60)
ck(row and row.get("unavailable") is True and row.get("stale") is True, "past the period end the reading is stale", row)
ck(row and "2026-10-22" in row.get("err", ""), "the stale reason names the period end", row)
gk = [p for p in [i for i in hub.foot_items(hub.foot_sources(END + 60), END + 60) if i["key"] == "subs"][0]["parts"] if p["text"].startswith("GK")][0]
ck(gk["text"] == "GK ?", "the footer token reads the stale reading as ? too", gk)
older = line("2026-10-17T06:00:00.000Z", "billing: credits config", ctx={"config": {
    "creditUsagePercent": 10.0, "currentPeriod": {"start": "2026-10-15T18:51:01Z", "end": "2026-10-22T18:51:01Z"}}})
write_log(older)
ck(hub.q_grok()["windows"][0]["pct"] == 73.0, "a log line older than the kept reading does not replace it")

print("== 8. a billing line with no creditUsagePercent is a 0% reading, not a missing one (#753)")
# grok 1.0.62 omits creditUsagePercent when the period's usage is 0%, so that line carries the period alone: a
# currentPeriod with start and end is a reading, and the field's absence on it means 0%. The previous period's
# kept 73% must not stand in for it.
NEW_START = dt.datetime(2026, 10, 22, 18, 51, 1, 60398, tzinfo=dt.timezone.utc).timestamp()
NEW_END = dt.datetime(2026, 10, 29, 18, 51, 1, 60398, tzinfo=dt.timezone.utc).timestamp()
write_log(line("2026-10-22T19:00:00.000Z", "billing: fetched credits config", src="shell", ctx={"config": {
    "currentPeriod": {"type": "USAGE_PERIOD_TYPE_WEEKLY",
                      "start": "2026-10-22T18:51:01.060398+00:00", "end": "2026-10-29T18:51:01.060398+00:00"}},
    "subscriptionTier": "SuperGrok"}))
r = hub.q_grok()
w = ((r or {}).get("windows") or [{}])[0]
ck(w.get("pct") == 0.0, "a line with no creditUsagePercent reads as 0%, not as the kept reading", r)
ck(w.get("resets") == NEW_END and (r or {}).get("until") == NEW_END, "the weekly window resets at the new period end", r)
ck(not (r or {}).get("unavailable"), "the reading is not unavailable", r)
kept = json.loads(KEPT.read_text()) if KEPT.exists() else {}
ck(kept.get("pct") == 0.0, "the 0% reading replaces the kept one, so a rotation cannot resurrect the old period", kept)
hub.quota = {"grok": r}
row = hub.quota_row("grok", NEW_START + 3600)
ck(row and not row.get("unavailable") and not row.get("stale"), "the panel row is not stale while the new period is open", row)
ck(row and (row.get("windows") or [{}])[0].get("pct") == 0.0, "the panel row reads 0%", row)

print(f"\n{checks - len(fails)}/{checks} checks passed")
if fails:
    print("FAILED: " + "; ".join(fails)); sys.exit(1)
print("SQUAD HUB QUOTA: PASS")
