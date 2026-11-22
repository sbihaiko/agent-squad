"""The polling schedule of each `guest` channel (SPEC 2682, #2709).

A tick fires every minute and does nothing before the `next_due` of the channel. Every case drives the schedule over a
sequence of clock values, so the rule is proven by the sequence and not by one reading. Every id and every `ts` here is
a placeholder.
"""
import copy
import json
from datetime import datetime, timedelta

from squad import slackpolicy
from squad import slackpoller as P

CLIENT = "U0CLIENT01"
NOW = datetime(2026, 11, 22, 12, 0, 0)
THREAD = "100.000100"

COMMITTED = {
    "version": 1,
    "project": {"language": "en-US", "persona": "iara"},
    "personas": [{"id": "iara", "name": "Iara", "voice": "Sulafat", "tone": "Warm and patient."}],
    "roles": [{"id": "colleague", "actions": ["status"]}],
    "workspaces": [],
}

LOCAL = {
    "version": 1,
    "workspaces": [
        {"id": "T0CLIENT01", "label": "client-a", "class": "guest", "persona": "iara",
         "channels": [
             {"id": "C0CLISUP01", "name": "support", "mode": "listen+speak", "speak": "draft",
              "roles": ["colleague"],
              "poll": {"mode": "two_state", "fast": "1m", "slow": "10m", "quiet_after": "10m",
                       "thread_open": "30m"}},
             {"id": "C0CLIGEN01", "name": "general", "mode": "listen",
              "poll": {"mode": "exponential", "floor": "5m", "ceiling": "1h", "factor": 2,
                       "idle_window": "15m", "thread_open": "30m"}},
         ]},
    ],
    "members": [{"workspace": "T0CLIENT01", "channel": "C0CLISUP01", "role": "colleague", "users": [CLIENT]}],
}

WS, CH, EXP = "T0CLIENT01", "C0CLISUP01", "C0CLIGEN01"

# An exponential channel whose floor times its factor already exceeds its ceiling, so the first idle step clamps.
CLAMPED = copy.deepcopy(LOCAL)
CLAMPED["workspaces"][0]["channels"][1]["poll"].update({"floor": "40m", "ceiling": "1h", "factor": 2})


def now_ts(value):
    """A Slack `ts` of one clock value: the epoch with a fractional part, as the connector writes it."""
    return f"{value.timestamp():.6f}"


def only(*ids):
    """The local file with the named channels only, so one case drives one channel."""
    local = copy.deepcopy(LOCAL)
    for ws in local["workspaces"]:
        ws["channels"] = [row for row in ws["channels"] if row["id"] in ids]
    return local


def schedule(tmp_path, threads=None, local=LOCAL):
    return P.PollSchedule(tmp_path / "schedule.json", COMMITTED, local, threads=threads)


WS2 = "T0CLIENT02"


def two_workspaces_one_channel():
    """Two guest workspaces that expose the same channel id, as two clients' connectors may."""
    local = copy.deepcopy(LOCAL)
    first = copy.deepcopy(local["workspaces"][0])
    second = copy.deepcopy(first)
    second["id"], second["label"] = WS2, "client-b"
    first["channels"] = [row for row in first["channels"] if row["id"] == CH]
    second["channels"] = [row for row in second["channels"] if row["id"] == CH]
    local["workspaces"] = [first, second]
    return local


def minutes(value):
    return timedelta(minutes=value)


# --- 1. a tick fires every minute and does nothing before next_due -------------------------------------------------

def test_a_tick_does_nothing_before_the_next_due_of_the_channel(tmp_path):
    s = schedule(tmp_path)
    assert s.due(NOW) == [(WS, CH), (WS, EXP)]      # a channel never polled is due at once
    s.polled(WS, CH, NOW)                           # two_state with no message: slow, ten minutes
    s.polled(WS, EXP, NOW)                          # exponential with no message: the floor, five minutes
    assert s.due(NOW + timedelta(seconds=59)) == []
    assert s.due(NOW + timedelta(minutes=4, seconds=59)) == []
    assert s.due(NOW + timedelta(minutes=5)) == [(WS, EXP)]
    assert s.due(NOW + timedelta(minutes=9, seconds=59)) == [(WS, EXP)]
    assert s.due(NOW + timedelta(minutes=10)) == [(WS, CH), (WS, EXP)]
    # The clock keeps the due time: a new schedule over the same file reads it back.
    again = schedule(tmp_path)
    assert (WS, CH) not in again.due(NOW + timedelta(minutes=9, seconds=59))
    assert again.next_due(WS, CH) == NOW + timedelta(minutes=10)


# --- 2. two_state stays fast after a message from another person ----------------------------------------------------

def test_two_state_stays_fast_after_a_message_from_another_person(tmp_path):
    s = schedule(tmp_path, local=only(CH))
    s.note_message(WS, CH, NOW)
    clock, intervals = NOW, []
    for _ in range(9):                              # nine polls, each one minute after the last message
        clock += minutes(1)
        assert s.due(clock) == [(WS, CH)]
        s.polled(WS, CH, clock)
        intervals.append(s.next_due(WS, CH) - clock)
    assert intervals == [minutes(1)] * 9


# --- 3. two_state changes to slow after quiet_after without a new message -------------------------------------------

def test_two_state_changes_to_slow_after_quiet_after(tmp_path):
    s = schedule(tmp_path, local=only(CH))
    s.note_message(WS, CH, NOW)
    clock = NOW
    for _ in range(10):                             # ten fast polls, the tenth exactly at quiet_after
        clock += minutes(1)
        assert s.due(clock) == [(WS, CH)]
        s.polled(WS, CH, clock)
    assert s.next_due(WS, CH) - clock == minutes(10)   # fast became slow
    assert s.due(clock + minutes(9)) == []
    assert s.due(clock + minutes(10)) == [(WS, CH)]


# --- 4. exponential starts at floor, multiplies by factor, stops at ceiling -----------------------------------------

def test_exponential_grows_from_the_floor_to_the_ceiling(tmp_path):
    s = schedule(tmp_path, local=only(EXP))
    s.note_message(WS, EXP, NOW)
    clock, intervals = NOW, []
    for _ in range(9):
        clock = s.next_due(WS, EXP)
        assert s.due(clock) == [(WS, EXP)]
        s.polled(WS, EXP, clock)
        intervals.append((s.next_due(WS, EXP) - clock).total_seconds() / 60)
    assert intervals == [5, 5, 10, 10, 20, 40, 60, 60, 60]


# --- 5. a new message returns the interval to fast ------------------------------------------------------------------

def test_a_new_message_returns_the_interval_to_fast(tmp_path):
    s = schedule(tmp_path, local=only(CH))
    s.note_message(WS, CH, NOW)
    quiet = NOW + minutes(30)
    assert s.due(quiet) == [(WS, CH)]
    s.polled(WS, CH, quiet)                         # a long silence makes the interval slow
    assert s.next_due(WS, CH) - quiet == minutes(10)
    fresh = quiet + timedelta(seconds=20)
    s.note_message(WS, CH, fresh)
    assert s.next_due(WS, CH) - fresh == minutes(1)


# --- 6. an announced event returns the interval to fast -------------------------------------------------------------

def test_an_announced_event_returns_the_interval_to_fast(tmp_path):
    s = schedule(tmp_path, local=only(CH))
    s.note_message(WS, CH, NOW)
    quiet = NOW + minutes(30)
    s.polled(WS, CH, quiet)
    assert s.next_due(WS, CH) - quiet == minutes(10)
    announce = quiet + minutes(2)
    s.note_event(WS, CH, announce)
    assert s.next_due(WS, CH) - announce == minutes(1)


# --- 7. an agent thread newer than thread_open keeps the interval fast ----------------------------------------------

def test_a_channel_with_no_message_waits_the_slow_interval(tmp_path):
    # A channel that never delivered a message is quiet, so two_state reads it at the slow interval and not every
    # minute: the schedule costs the quiet channel the quiet amount (stories 9 and 10).
    s = schedule(tmp_path, local=only(CH))
    assert s.due(NOW) == [(WS, CH)]                 # never polled: due at once
    s.polled(WS, CH, NOW)
    assert s.next_due(WS, CH) - NOW == minutes(10)


def test_a_stale_agent_thread_does_not_keep_the_interval_fast(tmp_path):
    # Only a thread message newer than thread_open keeps the interval fast. A thread whose last read is an hour old is
    # out of the window, so the channel falls back on its own silence.
    threads = P.RepliedThreads(tmp_path / "threads.json")
    threads.record(CH, THREAD, at=NOW - minutes(60), ts="100.000900", workspace=WS)
    s = schedule(tmp_path, threads=threads, local=only(CH))
    assert s.interval(WS, CH, NOW) == 600


def test_a_thread_in_one_workspace_does_not_keep_a_shared_channel_fast(tmp_path):
    # Two guest workspaces expose the same channel id, and a thread recorded under one of them must not keep the other
    # workspace's channel fast: only the channel's own thread newer than thread_open keeps it fast.
    threads = P.RepliedThreads(tmp_path / "threads.json")
    threads.record(CH, THREAD, at=NOW - minutes(2), ts="100.000900", workspace=WS)
    s = schedule(tmp_path, threads=threads, local=two_workspaces_one_channel())
    assert s.interval(WS, CH, NOW) == 60            # its own recent thread keeps it fast
    assert s.interval(WS2, CH, NOW) == 600          # the other workspace's channel stays quiet


def test_the_newest_open_thread_of_the_channel_decides_the_window(tmp_path):
    # The store answers one value per channel, the newest open thread, so a recent thread keeps the interval fast even
    # while an older thread sits past thread_open.
    threads = P.RepliedThreads(tmp_path / "threads.json")
    threads.record(CH, THREAD, at=NOW - minutes(60), ts="100.000900", workspace=WS)
    threads.record(CH, "100.000200", at=NOW - minutes(2), ts="100.001000", workspace=WS)
    s = schedule(tmp_path, threads=threads, local=only(CH))
    assert threads.last_at(CH, workspace=WS) == NOW - minutes(2)
    assert s.interval(WS, CH, NOW) == 60


def test_the_minute_tick_never_polls_before_the_next_due(tmp_path):
    # One tick a minute over a sequence of clock values: the tick answers a channel only on the minute its next_due
    # falls, and never before (story 9).
    s = schedule(tmp_path)
    assert s.due(NOW) == [(WS, CH), (WS, EXP)]
    s.polled(WS, CH, NOW)                           # no message: ten minutes
    s.polled(WS, EXP, NOW)                          # exponential: the five minute floor
    fired = {}
    for minute in range(1, 23):
        clock = NOW + minutes(minute)
        for channel in s.due(clock):
            fired.setdefault(channel[1], []).append(minute)
            s.polled(channel[0], channel[1], clock)
    assert fired[CH] == [10, 20]
    assert fired[EXP] == [5, 10, 15, 20]


def test_a_poll_well_after_the_message_measures_quiet_from_the_message(tmp_path):
    # The tick reaches the channel twenty minutes after the message. quiet_after counts from the message, so the poll
    # plans the slow interval, not the fast one it would plan if the tick's clock were the activity.
    s = schedule(tmp_path, local=only(CH))
    s.note_message(WS, CH, NOW)
    late = NOW + minutes(20)
    s.polled(WS, CH, late)
    assert s.next_due(WS, CH) - late == minutes(10)


def test_the_quiet_clock_runs_from_the_last_message_not_the_tick(tmp_path):
    # A message at NOW is still the anchor at NOW + 20 minutes: the interval is slow, because quiet_after counts from
    # the message and not from the clock value of the tick.
    s = schedule(tmp_path, local=only(CH))
    s.note_message(WS, CH, NOW)
    assert s.interval(WS, CH, NOW + timedelta(seconds=59)) == 60
    assert s.interval(WS, CH, NOW + minutes(10)) == 600
    assert s.interval(WS, CH, NOW + minutes(20)) == 600


def test_an_agent_thread_within_thread_open_keeps_the_interval_fast(tmp_path):
    threads = P.RepliedThreads(tmp_path / "threads.json")
    s = schedule(tmp_path, threads=threads, local=only(CH))
    s.note_message(WS, CH, NOW)
    threads.record(CH, THREAD, at=NOW + minutes(29), ts="100.000900", workspace=WS)
    quiet = NOW + minutes(31)                       # thirty-one minutes after the message, two after the thread
    plain = schedule(tmp_path / "plain", threads=None, local=only(CH))   # the same clock with no thread: slow
    plain.note_message(WS, CH, NOW)
    plain.polled(WS, CH, quiet)
    assert plain.next_due(WS, CH) - quiet == minutes(10)
    assert s.due(quiet) == [(WS, CH)]
    s.polled(WS, CH, quiet)
    assert s.next_due(WS, CH) - quiet == minutes(1)


# --- 8. a poll that delivers a new message notes it on the schedule --------------------------------------------------

def test_a_polled_message_sets_the_channel_next_due_through_the_schedule(tmp_path):
    # A poll that delivers a new message notes it on the schedule, so the channel's next poll is one fast interval away
    # and the schedule reads the channel fast until quiet_after. Found in review: the schedule knew nothing of a poll.
    s = schedule(tmp_path, local=only(CH))
    page = [{"ts": now_ts(NOW), "user": CLIENT, "text": "hello"}]
    p = P.Poller(lambda **request: {"ok": True, "messages": list(page)},
                 P.Checkpoint(tmp_path / "checkpoint.json"), COMMITTED, only(CH), lambda: NOW, schedule=s)
    assert [m["ts"] for m, _ in p.poll(WS, CH)] == [page[0]["ts"]]
    assert s.next_due(WS, CH) - NOW == minutes(1)
    assert s.interval(WS, CH, NOW) == 60
    assert s.interval(WS, CH, NOW + minutes(10)) == 600


# --- 9. the quiet-when-no-message rule lives in the schedule, not the shared policy ----------------------------------

def test_the_policy_keeps_its_fast_default_with_no_prior_message(tmp_path):
    # decide() keeps its fast default for a guest channel with no prior message, so the shared policy output never
    # changes silently. The schedule alone reads a channel with no message at the slow interval.
    row = only(CH)["workspaces"][0]["channels"][0]
    assert slackpolicy.poll_interval(NOW, row, {}) == 60
    s = schedule(tmp_path, local=only(CH))
    s.polled(WS, CH, NOW)
    assert s.next_due(WS, CH) - NOW == minutes(10)


# --- 10. the interval of a note is planned in the same write as the activity ---------------------------------------

def test_a_note_writes_the_last_activity_and_the_next_due_once(tmp_path, monkeypatch):
    # One write carries both fields, so an interrupted note never leaves a last activity with a stale due time.
    s = schedule(tmp_path, local=only(CH))
    docs = []
    real = P.atomic_write

    def capture(path, text):
        docs.append(json.loads(text))
        real(path, text)

    monkeypatch.setattr(P, "atomic_write", capture)
    s.note_message(WS, CH, NOW)
    assert len(docs) == 1
    entry = docs[0][WS][CH]
    assert entry["last_at"] == NOW.isoformat()
    assert entry["next_due"] == (NOW + minutes(1)).isoformat()


# --- 11. exponential clamps the first idle step at the ceiling -----------------------------------------------------

def test_exponential_clamps_the_first_step_at_the_ceiling(tmp_path):
    s = schedule(tmp_path, local=CLAMPED)
    s.note_message(WS, EXP, NOW)
    step = NOW + minutes(15)                        # one idle window: forty minutes times two passes the ceiling
    s.polled(WS, EXP, step)
    assert s.next_due(WS, EXP) - step == minutes(60)
