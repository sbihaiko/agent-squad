"""The live check's reading (`squad/livecheck.py`): the three invariants, from a fixture state shaped like the live one.

The end-to-end proof is `contrib/squad-goal/scripts/test-live-check.sh`, which drives the door the nightly runs. These cases
pin the reading itself: each invariant alone, and the window's sampling, with a clock the test owns, so none of them sleeps.
"""
import json
import os
import time

from squad import livecheck

SHA = "a" * 40
DEAD_PID = 99999999


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def history_line(state, finish, outcome, sha=SHA, scope="delta", ran="-", red="-"):
    """One gate-history line, gate.sh's 13 fields: finish, kind, result, sha, start, selection, scope, outcome, reason,
    ran, not run, slots, red."""
    line = f"{finish}\tfast\tfail\t{sha}\t{finish}\tsel\t{scope}\t{outcome}\treason\t{ran}\t-\t2\t{red}\n"
    state.mkdir(parents=True, exist_ok=True)
    with (state / "gate-history").open("a") as f:
        f.write(line)


def age(path, seconds):
    """Set the file's mtime `seconds` into the past: a wait's age is its marker's mtime."""
    then = time.time() - seconds
    os.utime(path, (then, then))


def obligation(state, key, deadline, sha=SHA, scope="full", checks="ruff"):
    write(state / "obligations" / key, f"o/r\t{sha}\tdev\t{checks}\t{scope}\t{deadline}\n")


def lock_wait(state, lock, pid, since):
    write(state / "locks" / "waiting" / f"{lock}-{pid}", f"{since}\t{lock}\t{pid}\n")


def slot_wait(caps, kind, holder, pid):
    write(caps / "waiting" / f"{kind}-{holder}.json", json.dumps({"ceiling": f"caps.{kind}", "holder": holder, "pid": pid}))


def lease(caps, kind, holder, pid):
    write(caps / kind / f"{holder}.json", json.dumps({"holder": holder, "pid": pid, "since": time.time()}))


def test_a_wait_past_the_bound_whose_every_slot_holder_is_queued_for_the_lock_is_a_starved_child(tmp_path):
    state, caps = tmp_path / "st", tmp_path / "caps"
    lock_wait(state, "gate", os.getpid(), int(time.time()) - 1000)
    lease(caps, "gates", "gate-1", os.getpid())
    slot_wait(caps, "gates", "run-1", os.getpid())
    [finding] = livecheck.starvation(state, caps, now=time.time() + 1000, bound=900)
    assert (finding.kind, finding.subject) == ("STARVED", "run-1")
    assert "queued for lock gate" in finding.detail


def test_the_four_field_marker_serial_sh_writes_is_a_queued_waiter(tmp_path):
    """serial.sh writes epoch, lock, pid and priority (#2887); the older three-field marker still reads."""
    state = tmp_path / "st"
    since = int(time.time())
    write(state / "locks" / "waiting" / f"gate-{os.getpid()}", f"{since}\tgate\t{os.getpid()}\t0\n")
    assert livecheck.lock_waits(state) == [(since, "gate", os.getpid())]
    lock_wait(state, "gate", os.getpid(), since)
    assert livecheck.lock_waits(state) == [(since, "gate", os.getpid())]


def test_a_wait_inside_the_bound_is_no_starvation(tmp_path):
    state, caps = tmp_path / "st", tmp_path / "caps"
    lock_wait(state, "gate", os.getpid(), int(time.time()))
    lease(caps, "gates", "gate-1", os.getpid())
    slot_wait(caps, "gates", "run-1", os.getpid())
    assert livecheck.starvation(state, caps, now=time.time(), bound=900) == []


def test_a_slot_holder_that_is_not_queued_for_the_lock_starves_no_one(tmp_path):
    state, caps = tmp_path / "st", tmp_path / "caps"
    lock_wait(state, "gate", os.getpid(), int(time.time()) - 1000)
    lease(caps, "gates", "gate-1", DEAD_PID)          # a holder that is not in the queue
    slot_wait(caps, "gates", "run-1", os.getpid())
    assert livecheck.starvation(state, caps, now=time.time() + 1000, bound=900) == []


def test_a_queue_marker_of_a_dead_process_is_no_queue(tmp_path):
    state, caps = tmp_path / "st", tmp_path / "caps"
    lock_wait(state, "gate", DEAD_PID, int(time.time()) - 1000)
    lease(caps, "gates", "gate-1", os.getpid())
    slot_wait(caps, "gates", "run-1", os.getpid())
    assert livecheck.starvation(state, caps, now=time.time() + 1000, bound=900) == []


def test_a_slot_wait_marker_of_a_dead_waiter_is_no_wait(tmp_path):
    """A marker left by a killed child must not age into a starvation: no one is queued for the slot any more."""
    state, caps = tmp_path / "st", tmp_path / "caps"
    lock_wait(state, "gate", os.getpid(), int(time.time()) - 1000)
    lease(caps, "gates", "gate-1", os.getpid())
    slot_wait(caps, "gates", "run-1", DEAD_PID)
    age(caps / "waiting" / "gates-run-1.json", 1000)
    assert livecheck.starvation(state, caps, now=time.time() + 1000, bound=900) == []


def test_an_unproven_entry_with_no_obligation_is_reported(tmp_path):
    state = tmp_path / "st"
    history_line(state, int(time.time()), "unproven")
    [finding] = livecheck.unproven(state, "o/r", since=0, now=time.time())
    assert (finding.kind, finding.subject) == ("UNPROVEN-NO-OBLIGATION", SHA)


def test_an_unproven_entry_with_a_future_obligation_is_clean(tmp_path):
    state, now = tmp_path / "st", time.time()
    history_line(state, int(now), "unproven")
    obligation(state, "111", int(now) + 900)
    assert livecheck.unproven(state, "o/r", since=0, now=now) == []


def test_an_unproven_entry_whose_obligation_expired_is_reported(tmp_path):
    state, now = tmp_path / "st", time.time()
    history_line(state, int(now), "unproven")
    obligation(state, "111", int(now) - 60)
    [finding] = livecheck.unproven(state, "o/r", since=0, now=now)
    assert (finding.kind, finding.subject) == ("UNPROVEN-EXPIRED", SHA)


def test_a_settled_entry_is_an_answered_obligation(tmp_path):
    state, now = tmp_path / "st", time.time()
    history_line(state, int(now), "unproven")
    obligation(state, "111", int(now) - 60)
    write(state / "obligation-settled" / "111", f"{int(now)}\to/r\t{SHA}\tdev\tfull\truff\tgreen\t1@tree\n")
    assert livecheck.unproven(state, "o/r", since=0, now=now) == []


def test_a_settlement_of_another_repo_at_the_same_tree_answers_nothing(tmp_path):
    """A profile works several repositories (ADR-0138): a settlement names its own tree in its own repository."""
    state, now = tmp_path / "st", time.time()
    history_line(state, int(now), "unproven")
    write(state / "obligation-settled" / "999", f"{int(now)}\tother/r\t{SHA}\tdev\tfull\truff\tgreen\t1@tree\n")
    [finding] = livecheck.unproven(state, "o/r", since=0, now=now)
    assert (finding.kind, finding.subject) == ("UNPROVEN-NO-OBLIGATION", SHA)


def test_an_entry_outside_the_window_is_history(tmp_path):
    state, now = tmp_path / "st", time.time()
    history_line(state, int(now) - 7200, "unproven")
    assert livecheck.unproven(state, "o/r", since=now - 3600, now=now) == []


def test_a_wave_entry_admitted_unproven_names_its_obligation(tmp_path):
    state, now = tmp_path / "st", time.time()
    entered = "entered wave/dev at abc; unproven: bounded; obligation 222 (ruff at full, base dev, deadline 1)"
    write(state / "state" / "o__r__7.json", json.dumps({"key": "o/r#7", "wave": {"run-9": {"entered": entered}}}))
    [finding] = livecheck.wave_entries(state, now)
    assert (finding.kind, finding.subject) == ("UNPROVEN-NO-OBLIGATION", "o/r#7")
    obligation(state, "222", int(now) + 900, sha="abc")
    assert livecheck.wave_entries(state, now) == []
    obligation(state, "222", int(now) - 60, sha="abc")
    [finding] = livecheck.wave_entries(state, now)
    assert (finding.kind, finding.subject) == ("UNPROVEN-EXPIRED", "o/r#7")


def test_a_wave_entry_whose_expired_obligation_was_settled_is_answered(tmp_path):
    """A settlement may be written before the obligation record is removed: the entry the nightly answered is no finding."""
    state, now = tmp_path / "st", time.time()
    entered = "entered wave/dev at abc; unproven: bounded; obligation 222 (ruff at full, base dev, deadline 1)"
    write(state / "state" / "o__r__7.json", json.dumps({"key": "o/r#7", "wave": {"run-9": {"entered": entered}}}))
    obligation(state, "222", int(now) - 60, sha="abc")
    write(state / "obligation-settled" / "222", f"{int(now)}\to/r\tabc\tdev\tfull\truff\tgreen\t1@tree\n")
    assert livecheck.wave_entries(state, now) == []


def test_a_red_the_record_still_holds_is_never_a_finding(tmp_path):
    state, now = tmp_path / "st", time.time()
    history_line(state, int(now) - 10, "fail", scope="delta", ran="ruff mypy pytest", red="red")
    history_line(state, int(now) - 5, "fail", scope="delta", ran="ruff", red="red-stands " + SHA)
    assert livecheck.reds(state, since=0) == []


def test_a_covering_pass_may_release_a_red(tmp_path):
    state, now = tmp_path / "st", time.time()
    history_line(state, int(now) - 10, "fail", scope="delta", ran="ruff mypy", red="red")
    history_line(state, int(now) - 5, "pass", scope="full", ran="ruff mypy pytest", red="cleared-green " + SHA)
    assert livecheck.reds(state, since=0) == []


def test_a_release_the_record_cannot_justify_is_reported(tmp_path):
    state, now = tmp_path / "st", time.time()
    history_line(state, int(now) - 10, "fail", scope="full", ran="ruff mypy pytest", red="red")
    history_line(state, int(now) - 5, "pass", scope="delta", ran="ruff mypy pytest", red="cleared-green " + SHA)
    [finding] = livecheck.reds(state, since=0)
    assert (finding.kind, finding.subject) == ("RED-RELEASED", SHA)


def test_a_pass_that_reran_fewer_checks_than_the_red_named_never_releases_it(tmp_path):
    state, now = tmp_path / "st", time.time()
    history_line(state, int(now) - 10, "fail", scope="full", ran="ruff mypy pytest", red="red")
    history_line(state, int(now) - 5, "pass", scope="full", ran="ruff", red="cleared-green " + SHA)
    assert [f.kind for f in livecheck.reds(state, since=0)] == ["RED-RELEASED"]


def test_the_window_is_sampled_and_every_finding_is_kept_once(tmp_path):
    state, caps = tmp_path / "st", tmp_path / "caps"
    lock_wait(state, "gate", os.getpid(), int(time.time()) - 1000)
    lease(caps, "gates", "gate-1", os.getpid())
    slot_wait(caps, "gates", "run-1", os.getpid())
    age(caps / "waiting" / "gates-run-1.json", 1000)   # the wait began long before the injected clock
    tick = [time.time()]

    def clock():
        return tick[0]

    def sleep(seconds):
        tick[0] += seconds

    findings, samples, _start = livecheck.run(state, caps, "o/r", window=1.0, interval=0.2, bound=900.0,
                                              clock=clock, sleep=sleep)
    assert samples >= 2
    assert [f.kind for f in findings] == ["STARVED"]        # one finding, however many samples saw it


def test_a_once_read_takes_one_sample(tmp_path):
    state, caps = tmp_path / "st", tmp_path / "caps"
    findings, samples, _start = livecheck.run(state, caps, "o/r", window=3600.0, interval=30.0, bound=900.0, once=True)
    assert (findings, samples) == ([], 1)


def test_the_record_is_one_line_written_by_rename(tmp_path):
    state = tmp_path / "st"
    livecheck.write_record(state, start=10, samples=3, findings=[livecheck.Finding("STARVED", "run-1", "waited")], now=99)
    fields = (state / "live-check-result").read_text().rstrip("\n").split("\t")
    assert fields[:4] == ["99", "fail", "10", "3"]
    assert json.loads(fields[4]) == ["STARVED run-1: waited"]
    assert not [p for p in state.iterdir() if p.name.startswith("live-check-result.")], "the temp file is gone"


def test_a_clean_read_exits_zero_and_a_finding_exits_one(tmp_path):
    state = tmp_path / "st"
    args = ["--state", str(state), "--caps", str(tmp_path / "caps"), "--repo", "o/r", "--now", "1000"]
    assert livecheck.main(args) == 0
    assert (state / "live-check-result").read_text().split("\t")[1] == "pass"
    history_line(state, 1000 - 10, "unproven")
    assert livecheck.main(args) == 1
    record = (state / "live-check-result").read_text()
    assert record.split("\t")[1] == "fail" and "UNPROVEN-NO-OBLIGATION" in record
