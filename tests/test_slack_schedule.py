"""The polling schedule of each `guest` channel (SPEC 2682, #2709).

A tick fires every minute and does nothing before the `next_due` of the channel. Every case drives the schedule over a
sequence of clock values, so the rule is proven by the sequence and not by one reading. Every id and every `ts` here is
a placeholder.
"""
import copy
from datetime import datetime, timedelta

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


def only(*ids):
    """The local file with the named channels only, so one case drives one channel."""
    local = copy.deepcopy(LOCAL)
    for ws in local["workspaces"]:
        ws["channels"] = [row for row in ws["channels"] if row["id"] in ids]
    return local


def schedule(tmp_path, threads=None, local=LOCAL):
    return P.PollSchedule(tmp_path / "schedule.json", COMMITTED, local, threads=threads)


def minutes(value):
    return timedelta(minutes=value)


# --- 1. a tick fires every minute and does nothing before next_due -------------------------------------------------

def test_a_tick_does_nothing_before_the_next_due_of_the_channel(tmp_path):
    s = schedule(tmp_path)
    assert s.due(NOW) == [(WS, CH), (WS, EXP)]      # a channel never polled is due at once
    s.polled(WS, CH, NOW)                           # two_state with no message: fast, one minute
    s.polled(WS, EXP, NOW)                          # exponential with no message: the floor, five minutes
    assert s.due(NOW + timedelta(seconds=59)) == []
    assert s.due(NOW + timedelta(minutes=1)) == [(WS, CH)]
    assert s.due(NOW + timedelta(minutes=4, seconds=59)) == [(WS, CH)]
    assert s.due(NOW + timedelta(minutes=5)) == [(WS, CH), (WS, EXP)]
    # The clock keeps the due time: a new schedule over the same file reads it back.
    again = schedule(tmp_path)
    assert again.due(NOW + timedelta(seconds=59)) == []
    assert again.next_due(WS, CH) == NOW + timedelta(minutes=1)


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

def test_an_agent_thread_within_thread_open_keeps_the_interval_fast(tmp_path):
    threads = P.RepliedThreads(tmp_path / "threads.json")
    s = schedule(tmp_path, threads=threads, local=only(CH))
    s.note_message(WS, CH, NOW)
    threads.record(CH, THREAD, at=NOW + minutes(29), ts="100.000900")
    quiet = NOW + minutes(31)                       # thirty-one minutes after the message, two after the thread
    plain = schedule(tmp_path / "plain", threads=None, local=only(CH))   # the same clock with no thread: slow
    plain.note_message(WS, CH, NOW)
    plain.polled(WS, CH, quiet)
    assert plain.next_due(WS, CH) - quiet == minutes(10)
    assert s.due(quiet) == [(WS, CH)]
    s.polled(WS, CH, quiet)
    assert s.next_due(WS, CH) - quiet == minutes(1)
