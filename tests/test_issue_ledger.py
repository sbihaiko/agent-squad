"""One ledger file per issue holds the loop's facts about it (#673): attempts per run, the last failure, the stop reason.

issue_ledger.py is the only writer of $SG_STATE/state/<owner>__<repo>__<n>.json. A write is atomic (ADR-0030), the key names
the repository (ADR-0138), a missing file of a new issue reads as todo with 0 attempts, and a missing file beside the old
requeues-N files holds the issue until `migrate` carries them in (ADR-0149 rule 4).
"""
import fcntl
import importlib.util
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "contrib" / "squad-goal" / "scripts"
SCRIPT = SCRIPTS / "issue_ledger.py"


def run(st, *args, repo="a/b"):
    env = {k: v for k, v in os.environ.items() if not k.startswith("SG_")}
    env.update(SG_STATE=str(st), SG_REPO=repo)
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, env=env, timeout=15)


def test_a_missing_file_reads_as_todo_with_no_attempts(tmp_path):
    r = run(tmp_path, "read", "a/b#7")
    assert r.returncode == 0, r.stderr
    d = json.loads(r.stdout)
    assert d["state"] == "todo" and d["attempts"] == 0
    assert run(tmp_path, "read", "a/b#7", "state").stdout.strip() == "todo"
    assert run(tmp_path, "count", "a/b#7").stdout.strip() == "0"
    assert not (tmp_path / "state").exists() or not list((tmp_path / "state").glob("*.json")), "a read writes nothing"


def test_the_file_is_keyed_by_repository_and_issue(tmp_path):
    assert run(tmp_path, "fail", "a/b#7", "r1", "", "the gates failed").stdout.split() == ["1", "1", "0"]
    assert (tmp_path / "state" / "a__b__7.json").is_file()
    d = json.loads((tmp_path / "state" / "a__b__7.json").read_text())
    assert d["key"] == "a/b#7" and [x["run"] for x in d["own"]["runs"]] == ["r1"]


def test_keys_from_two_repositories_do_not_collide(tmp_path):
    run(tmp_path, "fail", "a/b#7", "r1", "", "x")
    run(tmp_path, "fail", "a/b#7", "r2", "", "y")
    run(tmp_path, "fail", "c/d#7", "r9", "", "z", repo="c/d")
    assert run(tmp_path, "count", "a/b#7").stdout.strip() == "2"
    assert run(tmp_path, "count", "c/d#7", repo="c/d").stdout.strip() == "1"
    assert {p.name for p in (tmp_path / "state").glob("*.json")} == {"a__b__7.json", "c__d__7.json"}


def test_a_bare_issue_number_is_refused(tmp_path):
    r = run(tmp_path, "fail", "7", "r1", "", "x")
    assert r.returncode == 2 and not (tmp_path / "state").exists()


def test_one_run_counts_once_and_a_new_key_restarts_the_count(tmp_path):
    run(tmp_path, "fail", "a/b#7", "r1", "k1", "x")
    assert run(tmp_path, "fail", "a/b#7", "r1", "k1", "again").stdout.split() == ["1", "0", "0"]
    assert run(tmp_path, "fail", "a/b#7", "r2", "k1", "y").stdout.split() == ["2", "1", "0"]
    assert run(tmp_path, "fail", "a/b#7", "r3", "k2", "z").stdout.split() == ["1", "1", "0"]
    assert run(tmp_path, "read", "a/b#7", "last_fail").stdout.strip() == "z"


def test_stop_freezes_and_lists_every_run_and_a_new_run_counts_from_one(tmp_path):
    run(tmp_path, "fail", "a/b#7", "r1", "", "first")
    run(tmp_path, "fail", "a/b#7", "", "", "no run")
    r = run(tmp_path, "stop", "a/b#7", "the cap")
    assert r.stdout.splitlines() == ["- r1: first", "- (no run named): no run"]
    assert run(tmp_path, "read", "a/b#7", "state").stdout.strip() == "frozen"
    assert run(tmp_path, "fail", "a/b#7", "r1", "", "dup").stdout.split() == ["2", "0", "1"]
    assert run(tmp_path, "fail", "a/b#7", "r4", "", "owner let it start").stdout.split() == ["1", "1", "0"]


def test_the_loop_ledger_is_apart_and_its_stop_is_needs_human(tmp_path):
    run(tmp_path, "fail", "a/b#7", "r1", "", "adapter_error", "loop")
    assert run(tmp_path, "count", "a/b#7").stdout.strip() == "0"
    assert run(tmp_path, "count", "a/b#7", "loop").stdout.strip() == "1"
    run(tmp_path, "stop", "a/b#7", "loop cap", "loop")
    assert run(tmp_path, "read", "a/b#7", "state").stdout.strip() == "needs-human"


def test_rearm_clears_the_facts_and_queues_the_comment_removal(tmp_path):
    run(tmp_path, "fail", "a/b#7", "r1", "", "x")
    run(tmp_path, "stop", "a/b#7", "cap")
    assert run(tmp_path, "rearm", "a/b#7").returncode == 0
    d = json.loads(run(tmp_path, "read", "a/b#7").stdout)
    assert d["state"] == "todo" and d["attempts"] == 0
    q = list((tmp_path / "cq").glob("a__b#7-unstart~*.md"))
    assert len(q) == 1, list((tmp_path / "cq").iterdir())


def test_a_rearm_keeps_a_floor_so_an_old_run_never_counts_again(tmp_path):
    """#692: a rearm cleared every run, so the next janitor pass counted the old runs again and stopped the issue at once."""
    run(tmp_path, "fail", "a/b#7", "r1", "", "x")
    run(tmp_path, "fail", "a/b#7", "l1", "", "adapter_error", "loop")
    run(tmp_path, "rearm", "a/b#7")
    d = json.loads(run(tmp_path, "read", "a/b#7").stdout)
    assert d["attempts"] == 0 and set(d["rearmed"]["runs"]) == {"r1", "l1"} and d["rearmed"]["ts"] > 0
    assert run(tmp_path, "fail", "a/b#7", "r1", "", "x").stdout.split() == ["0", "0", "0"], "an old run counts nothing"
    assert run(tmp_path, "fail", "a/b#7", "l1", "", "x", "loop").stdout.split() == ["0", "0", "0"]
    assert run(tmp_path, "fail", "a/b#7", "r9", "", "y").stdout.split() == ["1", "1", "0"], "a new run counts"
    run(tmp_path, "rearm", "a/b#7")
    assert set(json.loads(run(tmp_path, "read", "a/b#7").stdout)["rearmed"]["runs"]) == {"r1", "l1", "r9"}, "a floor keeps the old one"


def test_a_missing_ledger_beside_the_old_counters_holds_until_migrate(tmp_path):
    """ADR-0149 rule 4: a missing ledger never falls back to the old counters. Beside them it holds the issue, and the
    owner's `migrate` carries them in once (or `rearm` clears them)."""
    a = tmp_path / "auto"
    a.mkdir()
    (a / "requeues-543").write_text("1\n")
    (a / "requeues-543.runs").write_text("r1\tk\tfirst\nr2\tk\tsecond\nfrozen\n")
    (a / "loopqueues-543.runs").write_text("l1\t\tadapter_error\n")
    d = json.loads(run(tmp_path, "read", "a/b#543").stdout)
    assert d["state"] == "held" and "migrate" in d["held"] and "requeues-543" in d["held"], d
    assert run(tmp_path, "fail", "a/b#543", "r3", "k", "x").returncode == 3, "a writer never reads the old counters"
    assert not (tmp_path / "state" / "a__b__543.json").exists()
    assert run(tmp_path, "migrate", "a/b#543").returncode == 0
    d = json.loads(run(tmp_path, "read", "a/b#543").stdout)
    assert d["state"] == "frozen" and d["attempts"] == 1, "the counter wins over the line count: the owner's reset holds"
    assert run(tmp_path, "count", "a/b#543", "loop").stdout.strip() == "1"
    assert run(tmp_path, "read", "a/b#543", "last_fail").stdout.strip() == "second"
    assert run(tmp_path, "fail", "a/b#543", "r2", "k", "dup").stdout.split() == ["1", "0", "1"], "a migrated run counts once"
    assert run(tmp_path, "migrate", "a/b#543").returncode != 0, "a ledger that exists is never migrated over"
    assert run(tmp_path, "read", "c/d#543", "state", repo="a/b").stdout.strip() == "todo", "the old files are the primary repository's"


def test_a_rearm_frees_a_missing_ledger_beside_the_old_counters(tmp_path):
    (tmp_path / "auto").mkdir()
    (tmp_path / "auto" / "requeues-9").write_text("3\n")
    assert run(tmp_path, "read", "a/b#9", "state").stdout.strip() == "held"
    run(tmp_path, "rearm", "a/b#9")
    assert run(tmp_path, "read", "a/b#9", "state").stdout.strip() == "todo", "after a write the old files are read no more"


def test_a_kill_in_the_middle_of_a_write_leaves_the_old_file_whole(tmp_path):
    run(tmp_path, "fail", "a/b#7", "r1", "", "x")
    f = tmp_path / "state" / "a__b__7.json"
    before = f.read_bytes()
    code = (
        "import os, signal, sys; sys.path.insert(0, sys.argv[1]); import issue_ledger as L\n"
        "os.fsync = lambda fd: os.kill(os.getpid(), signal.SIGKILL)\n"
        "L.fail('a/b#7', 'r2', '', 'y')\n"
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith("SG_")}
    env.update(SG_STATE=str(tmp_path), SG_REPO="a/b")
    p = subprocess.run([sys.executable, "-c", code, str(SCRIPTS)], env=env, capture_output=True, timeout=15)
    assert p.returncode == -9, p.stderr
    assert f.read_bytes() == before
    assert json.loads(f.read_text())["own"]["count"] == 1
    assert run(tmp_path, "fail", "a/b#7", "r3", "", "z").stdout.split() == ["2", "1", "0"], "the lock a dead writer held is free"


@pytest.mark.parametrize("bad", ["", "a/b", "a/b#", "a#7", "a/b#x"])
def test_a_key_with_no_repository_and_number_is_refused(tmp_path, bad):
    assert run(tmp_path, "read", bad).returncode == 2


def test_a_landing_is_a_fact_keyed_by_its_run_or_its_branch(tmp_path):
    """#695: the landing pass writes the issue's delivery as a `landed` fact, and rule R reads it, never a run's land heads."""
    assert run(tmp_path, "landed", "a/b#7", "run-1", "run", "abc").stdout.strip() == "recorded"
    assert run(tmp_path, "landed", "a/b#7", "run-1", "run", "abc").stdout.strip() == "duplicate", "a landing counts once"
    assert run(tmp_path, "landed", "a/b#8", "fix/8-x", "branch", "def").stdout.strip() == "recorded"
    assert sorted(run(tmp_path, "landed", "a/b").stdout.splitlines()) == ["fix/8-x\t8", "run-1\t7"]
    assert run(tmp_path, "landed", "a/b#7", "run-1", "wave", "abc").returncode == 2, "only a run or a branch lands"


def test_a_landing_and_a_requeue_head_follow_a_rearm_floor(tmp_path):
    """#695: the old auto/requeued- marker becomes a `requeue` fact, and both facts go with a rearm."""
    run(tmp_path, "landed", "a/b#7", "run-1", "run", "abc")
    run(tmp_path, "requeue", "a/b#7", "run-1", "abc")
    run(tmp_path, "rearm", "a/b#7")
    d = json.loads(run(tmp_path, "read", "a/b#7").stdout)
    assert d["landed"] == {} and d["requeued"] == {}
    assert run(tmp_path, "landed", "a/b#7", "run-1", "run", "abc").stdout.strip() == "floor"
    assert run(tmp_path, "requeue", "a/b#7", "run-1", "abc").stdout.strip() == "floor"


def test_the_head_a_run_requeued_the_issue_on_is_a_fact(tmp_path):
    """#695: childcc's requeue waits for a new head; the head it was requeued on is a ledger fact."""
    assert run(tmp_path, "requeue", "a/b#7", "run-1", "abc").stdout.strip() == "recorded"
    assert run(tmp_path, "requeue", "a/b#7", "run-1", "abc").stdout.strip() == "duplicate"
    assert run(tmp_path, "requeue", "a/b#7", "run-1", "def").stdout.strip() == "recorded", "a new head records"
    assert run(tmp_path, "requeued", "a/b#7", "run-1").stdout.strip() == "def"
    assert run(tmp_path, "requeued", "a/b#7", "run-2").stdout.strip() == ""


def review(st, *args):
    """One review verdict of one run, as the loop's review gate sends it."""
    return run(st, "review", "a/b#7", *args).stdout.strip()


def test_three_changes_verdicts_freeze_the_issue_as_needs_human(tmp_path):
    """#702 (ADR-0151 rule 1): three consecutive reviews that ask for changes end the issue needs-human for the owner,
    instead of another dispatch, and an approval in between starts the count again."""
    assert [review(tmp_path, r, "changes", f"finding-{r}") for r in ("r1", "r2", "r3")] == ["recorded", "recorded", "frozen"]
    d = json.loads(run(tmp_path, "read", "a/b#7", "review").stdout)
    assert d["changes"] == 3 and d["stopped"] and "changes" in d["stopped"]
    assert run(tmp_path, "read", "a/b#7", "state").stdout.strip() == "needs-human", "the ledger holds its dispatch"
    assert review(tmp_path, "r4", "changes", "finding-r4") == "recorded", "a stopped issue still records its reviews"
    assert run(tmp_path, "read", "a/b#7", "state").stdout.strip() == "needs-human", "an approval does not lift the owner's freeze"


def test_an_approve_resets_the_review_streak(tmp_path):
    assert [review(tmp_path, r, v, f) for r, v, f in (("r1", "changes", "f1"), ("r2", "changes", "f2"),
                                                      ("r3", "approve", ""), ("r4", "changes", "f3"),
                                                      ("r5", "changes", "f4"))] == ["recorded", "recorded", "reset",
                                                                                   "recorded", "recorded"]
    d = json.loads(run(tmp_path, "read", "a/b#7", "review").stdout)
    assert d["changes"] == 2 and not d["stopped"], "two after the approval is no streak of three"
    assert run(tmp_path, "read", "a/b#7", "state").stdout.strip() == "todo"


def test_identical_findings_twice_move_the_coder_one_place(tmp_path):
    """#702 (ADR-0151 rule 2): the same findings twice stop the fix cycle, so the next attempt starts one place down the
    coder chain; a third identical round moves it no further, and a new finding starts the count again."""
    assert run(tmp_path, "coder", "a/b#7").stdout.strip() == "0"
    assert review(tmp_path, "r1", "changes", "the same finding") == "recorded"
    assert run(tmp_path, "coder", "a/b#7").stdout.strip() == "0", "one review asks for no different coder"
    assert review(tmp_path, "r2", "changes", "the same finding") == "recorded"
    assert run(tmp_path, "coder", "a/b#7").stdout.strip() == "1"
    assert review(tmp_path, "r3", "changes", "the same finding") == "frozen"
    assert run(tmp_path, "coder", "a/b#7").stdout.strip() == "1", "a third identical round moves no coder a second time"
    assert review(tmp_path, "r4", "approve", "") == "reset"
    assert run(tmp_path, "coder", "a/b#7").stdout.strip() == "0", "an approval starts the coder at the top of the chain"
    assert review(tmp_path, "r5", "changes", "a new finding") == "recorded"
    assert review(tmp_path, "r6", "changes", "another finding") == "recorded"
    assert run(tmp_path, "coder", "a/b#7").stdout.strip() == "0", "two different findings change no coder"


def test_a_review_verdict_is_one_fact_per_run_and_round(tmp_path):
    """A retry of one round is no second verdict; a fix round of the same run is one."""
    assert run(tmp_path, "review", "a/b#7", "r1", "changes", "f1", "1").stdout.strip() == "recorded"
    assert run(tmp_path, "review", "a/b#7", "r1", "changes", "f1", "1").stdout.strip() == "duplicate"
    assert json.loads(run(tmp_path, "read", "a/b#7", "review").stdout)["changes"] == 1
    assert run(tmp_path, "review", "a/b#7", "r1", "changes", "f1", "2").stdout.strip() == "recorded"
    assert run(tmp_path, "review", "a/b#7", "r1", "blocked", "f1", "3").returncode == 2, "only changes and approve"


def test_a_review_fact_follows_a_rearm_floor_and_a_rearm_clears_it(tmp_path):
    review(tmp_path, "r1", "changes", "f1")
    review(tmp_path, "r2", "changes", "f2")
    run(tmp_path, "rearm", "a/b#7")
    d = json.loads(run(tmp_path, "read", "a/b#7", "review").stdout)
    assert d["changes"] == 0 and d["moves"] == 0 and not d["stopped"] and d["verdicts"] == []
    assert review(tmp_path, "r1", "changes", "f1") == "floor", "a run from before the rearm counts nothing"
    assert review(tmp_path, "r9", "changes", "f9") == "recorded"


def _streak(st, key="a/b#7"):
    return json.loads(run(st, "read", key, "review").stdout)


def test_the_review_fact_stores_the_keys_of_its_blocking_findings(tmp_path):
    """#2959: the review fact keeps one key per Blocking finding, and the resolved keys of a fix round."""
    assert review(tmp_path, "r1", "changes", "f1", "1", "k1,k2", "") == "recorded"
    assert review(tmp_path, "r1", "changes", "f2", "2", "k3", "k1,k2") == "recorded"
    v = _streak(tmp_path)["verdicts"]
    assert v[0]["keys"] == ["k1", "k2"] and v[0]["resolved"] == [], v
    assert v[1]["keys"] == ["k3"] and v[1]["resolved"] == ["k1", "k2"], v


def test_a_round_that_resolves_every_previous_finding_restarts_the_streak(tmp_path):
    """#2959 (ADR-0151 rule 1): a CHANGES round whose Resolved section lists the key of every previous Blocking finding
    raised only new findings, so the streak counts it as the first of a new run."""
    review(tmp_path, "r1", "changes", "f1", "1", "k1,k2", "")
    review(tmp_path, "r1", "changes", "f2", "2", "k3", "k1,k2")
    assert _streak(tmp_path)["changes"] == 1
    review(tmp_path, "r1", "changes", "f3", "3", "k4", "k3")
    review(tmp_path, "r1", "changes", "f4", "4", "k5", "k4")
    d = _streak(tmp_path)
    assert d["changes"] == 1 and not d["stopped"], "a branch that fixes each round's findings is never frozen"


def test_a_round_that_leaves_a_previous_finding_unresolved_adds_one(tmp_path):
    """#2959: a dropped, reworded or still open finding is not listed under Resolved, so the round adds 1."""
    review(tmp_path, "r1", "changes", "f1", "1", "k1,k2", "")
    review(tmp_path, "r1", "changes", "f2", "2", "k2b", "k1")   # k2 reworded as k2b: not resolved
    assert _streak(tmp_path)["changes"] == 2
    assert review(tmp_path, "r1", "changes", "f3", "3", "k9", "k2b,k7") == "recorded"
    d = _streak(tmp_path)
    assert d["changes"] == 1, "every previous key resolved, extra resolved keys do no harm"


def test_a_full_review_with_no_resolved_section_adds_one(tmp_path):
    review(tmp_path, "r1", "changes", "f1", "1", "k1", "")
    review(tmp_path, "r2", "changes", "f2", "0", "k2", "")
    assert _streak(tmp_path)["changes"] == 2
    assert review(tmp_path, "r3", "changes", "f3", "0", "k3", "") == "frozen", "three counted CHANGES still freeze"


def test_a_verdict_with_no_stored_keys_counts_as_before(tmp_path):
    review(tmp_path, "r1", "changes", "f1")                      # a verdict from before #2959: no keys
    review(tmp_path, "r1", "changes", "f2", "2", "k2", "k1")
    assert _streak(tmp_path)["changes"] == 2
    review(tmp_path, "r1", "changes", "f3", "3")                 # a new verdict with no keys
    assert _streak(tmp_path)["changes"] == 3


def test_the_whole_review_fingerprint_still_moves_the_coder_and_the_replay_agrees(tmp_path):
    """#2959: a restart of the streak leaves the same-findings-twice rule on the whole-review fingerprint (ADR-0151
    rule 2, ADR-0134), and the replay rebuilds the restarted streak from the stored keys."""
    review(tmp_path, "r1", "changes", "same", "1", "k1", "")
    review(tmp_path, "r1", "changes", "same", "2", "k1", "k1")
    d = _streak(tmp_path)
    assert d["changes"] == 1 and d["seen"] == 2 and d["moves"] == 1, d
    assert run(tmp_path, "coder", "a/b#7").stdout.strip() == "1"
    r = run(tmp_path, "replay", "a/b#7")
    assert r.returncode == 0, (r.stdout, r.stderr)


def test_replay_agrees_with_a_ledger_its_events_rebuild(tmp_path):
    """ADR-0149 rule 6: a prefix goes only after a replay of the ledger gives the same states."""
    run(tmp_path, "fail", "a/b#60", "r1", "k", "first")
    run(tmp_path, "fail", "a/b#60", "r2", "k", "second")
    for i in (1, 2, 3):
        run(tmp_path, "review", "a/b#60", f"v{i}", "changes", "same", str(i))
    r = run(tmp_path, "replay", "a/b#60")
    assert r.returncode == 0, r.stderr
    assert "a/b#60" in r.stdout and "ok" in r.stdout
    assert run(tmp_path, "replay", "a/b#61").returncode == 0, "a new issue replays to the seed"


def test_replay_names_the_issue_when_the_stored_state_differs(tmp_path):
    run(tmp_path, "fail", "a/b#62", "r1", "k", "first")
    f = tmp_path / "state" / "a__b__62.json"
    d = json.loads(f.read_text())
    d["own"]["count"] = 5
    f.write_text(json.dumps(d))
    r = run(tmp_path, "replay", "a/b#62")
    assert r.returncode == 1 and "a/b#62" in r.stderr and "own.count" in r.stderr, (r.stdout, r.stderr)


def test_replay_names_the_issue_when_the_review_streak_differs(tmp_path):
    run(tmp_path, "review", "a/b#63", "v1", "changes", "f", "1")
    f = tmp_path / "state" / "a__b__63.json"
    d = json.loads(f.read_text())
    d["review"]["changes"] = 0
    f.write_text(json.dumps(d))
    r = run(tmp_path, "replay", "a/b#63")
    assert r.returncode == 1 and "a/b#63" in r.stderr and "review.changes" in r.stderr, (r.stdout, r.stderr)


def test_replay_compares_the_ledger_with_its_legacy_counters(tmp_path):
    run(tmp_path, "fail", "a/b#64", "r1", "k", "first")
    a = tmp_path / "auto"
    a.mkdir()
    (a / "requeues-64").write_text("1\n")
    (a / "requeues-64.runs").write_text("r1\tk\tfirst\n")
    assert run(tmp_path, "replay", "a/b#64").returncode == 0, "the counters agree"
    (a / "requeues-64").write_text("4\n")
    r = run(tmp_path, "replay", "a/b#64")
    assert r.returncode == 1 and "a/b#64" in r.stderr and "requeues-64" in r.stderr, (r.stdout, r.stderr)


def test_replay_of_a_migrated_ledger_agrees_with_the_counter_the_owner_reset(tmp_path):
    a = tmp_path / "auto"
    a.mkdir()
    (a / "requeues-65").write_text("1\n")
    (a / "requeues-65.runs").write_text("r1\tk\tfirst\nr2\tk\tsecond\n")
    run(tmp_path, "migrate", "a/b#65")
    assert run(tmp_path, "replay", "a/b#65").returncode == 0


def test_replay_of_a_held_ledger_is_held_not_a_pass(tmp_path):
    f = tmp_path / "state" / "a__b__66.json"
    f.parent.mkdir()
    f.write_text("{")
    r = run(tmp_path, "replay", "a/b#66")
    assert r.returncode == 3 and "a/b#66" in r.stderr, (r.stdout, r.stderr)


def _legacy(tmp_path, n, lines):
    a = tmp_path / "auto"
    a.mkdir(exist_ok=True)
    (a / f"requeues-{n}").write_text("1\n")
    (a / f"requeues-{n}.runs").write_text(lines)


@pytest.mark.parametrize("old", ["r1\tother-key\tfirst\n", "r1\tk\tchanged reason\n"])
def test_replay_names_a_legacy_failure_whose_key_or_reason_differs(tmp_path, old):
    run(tmp_path, "fail", "a/b#67", "r1", "k", "first")
    _legacy(tmp_path, 67, old)
    r = run(tmp_path, "replay", "a/b#67")
    assert r.returncode == 1 and "a/b#67" in r.stderr and "own.runs" in r.stderr, (r.stdout, r.stderr)


def test_replay_names_a_legacy_frozen_the_ledger_does_not_hold(tmp_path):
    run(tmp_path, "fail", "a/b#68", "r1", "k", "first")
    _legacy(tmp_path, 68, "r1\tk\tfirst\nfrozen\n")
    r = run(tmp_path, "replay", "a/b#68")
    assert r.returncode == 1 and "a/b#68" in r.stderr and "frozen" in r.stderr, (r.stdout, r.stderr)


def test_replay_agrees_with_a_legacy_frozen_the_migrated_ledger_holds(tmp_path):
    _legacy(tmp_path, 69, "r1\tk\tfirst\nfrozen\n")
    run(tmp_path, "migrate", "a/b#69")
    assert run(tmp_path, "replay", "a/b#69").returncode == 0


def test_replay_names_a_stored_review_stop_its_events_do_not_support(tmp_path):
    run(tmp_path, "review", "a/b#70", "v1", "changes", "f", "1")
    f = tmp_path / "state" / "a__b__70.json"
    d = json.loads(f.read_text())
    d["review"]["stopped"] = "3 reviews of the issue asked for changes"
    f.write_text(json.dumps(d))
    r = run(tmp_path, "replay", "a/b#70")
    assert r.returncode == 1 and "a/b#70" in r.stderr and "review.stopped" in r.stderr, (r.stdout, r.stderr)


def import_ledger(name):
    """The script as a module, imported now: the mux pins the profile env around the hub's import, so the import is where a
    value read from the environment is true."""
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_state_dir_survives_the_hub_restoring_the_shell_env(tmp_path, monkeypatch):
    """The mux pins the profile's env around the hub's import and restores the shell's after it, so a module that reads
    SG_STATE at request time must still answer: /api/load closed the connection with KeyError and the Monitor lost every
    reading (#1073)."""
    monkeypatch.setenv("SG_STATE", str(tmp_path))
    mod = import_ledger("il_pinned")
    monkeypatch.delenv("SG_STATE")
    assert mod.state_dir() == tmp_path


def test_a_state_dir_with_no_profile_is_refused_by_name(monkeypatch):
    monkeypatch.delenv("SG_STATE", raising=False)
    mod = import_ledger("il_unpinned")
    with pytest.raises(RuntimeError, match="SG_STATE"):
        mod.state_dir()


def test_a_slower_footer_pass_never_replaces_a_newer_open_cache(tmp_path, monkeypatch):
    """Two footer threads share one ledger. The pass that read its signature first stalls, a newer pass publishes, and the
    stalled pass then finishes: it must not put its older signature over the newer one (#1746)."""
    mod = import_ledger("il_open_race")
    d = tmp_path
    (d / "state").mkdir()
    f = d / "state" / "o__r__1.json"

    def write(episodes):
        f.write_text(json.dumps({"own": {"count": 0, "runs": [], "stopped": None},
                                 "loop": {"count": 0, "runs": [], "stopped": None}, "episodes": episodes}))

    write([{"run": "run-a", "cause": "gate lock held by pid 9", "start": 900.0, "count": 1}])
    real_signature = mod._state_signature
    stalled, release = threading.Event(), threading.Event()

    def signature_then_stall(path):
        sig = real_signature(path)
        if threading.current_thread().name == "footer-a":
            stalled.set()
            release.wait(5)
        return sig

    monkeypatch.setattr(mod, "_state_signature", signature_then_stall)
    slow = threading.Thread(target=mod.open_episodes, kwargs={"state": d, "now": 1000.0}, name="footer-a")
    slow.start()
    assert stalled.wait(5)
    write([{"run": "run-a", "cause": "gate lock held by pid 9", "start": 900.0, "count": 1},
           {"run": "run-b", "cause": "gate slots are full", "start": 950.0, "count": 1}])
    newer = mod.open_episodes(state=d, now=1000.0)
    release.set()
    slow.join(5)
    assert len(newer["episodes"]) == 2
    current = real_signature(d)
    assert any(part == current for part in mod._OPEN_CACHE[str(d)]), \
        "the cache holds an older signature than the pass that published last"


class _Clock:
    def __init__(self, now):
        self.now = now

    def time(self):
        return self.now


def _in_process(tmp_path, monkeypatch, now=1000.0):
    monkeypatch.setenv("SG_STATE", str(tmp_path))
    monkeypatch.setenv("SG_REPO", "a/b")
    mod = import_ledger("il_episode")
    clock = _Clock(now)
    monkeypatch.setattr(mod, "time", clock)
    return mod, clock


@pytest.mark.parametrize("state", ["held", "stuck"])
def test_forty_polls_of_one_stuck_child_are_one_episode_counted_forty(tmp_path, monkeypatch, state):
    """A hold is one ledger episode, not a polling event (#1167): each poll of the same cause is an observation of it."""
    mod, _ = _in_process(tmp_path, monkeypatch)
    outs = [mod.wave("a/b#7", "r1", state, "gate lock held by pid 9") for _ in range(40)]
    assert outs[0] == "recorded" and set(outs[1:]) == {"observed"}
    assert [(e["run"], e["count"], e["cause"], e["open"]) for e in mod.read("a/b#7")["episodes"]] == [
        ("r1", 40, "gate lock held by pid 9", True)]


def test_a_repeated_observation_writes_only_its_count(tmp_path, monkeypatch):
    mod, _ = _in_process(tmp_path, monkeypatch)
    mod.wave("a/b#7", "r1", "held", "gate lock")
    before = json.loads(mod.path("a/b#7").read_text())
    assert mod.wave("a/b#7", "r1", "held", "gate lock") == "observed"
    after = json.loads(mod.path("a/b#7").read_text())
    before["episodes"][0]["count"] = 2
    assert after == before, "the file changes in the count and nothing else"


def test_a_rearm_closes_the_episode_and_a_later_hold_opens_a_new_one(tmp_path, monkeypatch):
    mod, clock = _in_process(tmp_path, monkeypatch, now=1000.0)
    for _ in range(3):
        mod.wave("a/b#7", "r1", "held", "gate lock")
    clock.now = 1100.0
    mod.rearm("a/b#7")
    clock.now = 1300.0
    for _ in range(2):
        mod.wave("a/b#7", "r9", "held", "gate lock")
    clock.now = 5000.0
    first, second = mod.read("a/b#7")["episodes"]
    assert (first["run"], first["count"], first["start"], first["open"], first["age"]) == ("r1", 3, 1000.0, False, 100.0)
    assert (second["run"], second["count"], second["start"], second["open"], second["age"]) == ("r9", 2, 1300.0, True, 3700.0)


def test_a_writer_with_a_stale_episode_key_is_refused_and_writes_nothing(tmp_path, monkeypatch):
    mod, _ = _in_process(tmp_path, monkeypatch, now=1000.0)
    assert mod.wave("a/b#7", "r1", "held", "gate lock") == "recorded"
    assert mod.wave("a/b#7", "r1", "held", "gate lock", start=1000.0) == "observed", "the live key folds in"
    before = mod.path("a/b#7").read_bytes()
    assert mod.wave("a/b#7", "r1", "held", "gate lock", start=555.0) == "stale"
    assert mod.path("a/b#7").read_bytes() == before


def test_a_writer_that_cannot_take_the_issue_lock_is_refused_not_waited_for(tmp_path, monkeypatch):
    mod, _ = _in_process(tmp_path, monkeypatch)
    mod.wave("a/b#7", "r1", "held", "gate lock")
    before = mod.path("a/b#7").read_bytes()
    with open(str(mod.path("a/b#7")) + ".lock", "a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        assert mod.wave("a/b#7", "r1", "held", "gate lock", wait=False) == "locked"
    assert mod.path("a/b#7").read_bytes() == before


def test_the_reader_sees_the_count_the_cause_and_the_age_from_the_ledger_alone(tmp_path):
    for _ in range(3):
        run(tmp_path, "wave", "a/b#7", "r1", "held", "gate lock")
    eps = json.loads(run(tmp_path, "read", "a/b#7", "episodes").stdout)
    assert len(eps) == 1 and eps[0]["count"] == 3 and eps[0]["cause"] == "gate lock"
    assert 0 <= eps[0]["age"] < 60


def test_a_board_event_carries_its_instant_its_column_and_its_writer(tmp_path):
    """One write path records a card write before it lands (#1841): the event is keyed by the issue and carries the
    instant it was recorded, the column it targets, and the writer's name. A writer that names no run names itself."""
    assert run(tmp_path, "board", "a/b#7", "janitor", "Doing", "", "1700000000", "janitor").stdout.strip() == "recorded"
    fact = json.loads((tmp_path / "state" / "a__b__7.json").read_text())["board"]
    assert fact["state"] == "pending"
    assert (fact["run"], fact["column"], fact["instant"], fact["by"]) == ("janitor", "Doing", 1700000000.0, "janitor")


def test_a_pending_write_is_a_prediction_with_its_writer(tmp_path):
    """The reader of the board's next read asks the ledger for each write GitHub has not proven: the issue, the column,
    the instant the writer recorded it, and the writer's name (#1841)."""
    run(tmp_path, "board", "a/b#7", "janitor", "Doing", "", "1700000000", "janitor")
    run(tmp_path, "board", "a/b#8", "reconcile", "Closed", "", "1700000500", "reconcile")
    run(tmp_path, "board", "c/d#7", "janitor", "Doing", "", "1700000000", "janitor", repo="c/d")
    rows = [line.split("\t") for line in run(tmp_path, "pending", "a/b").stdout.splitlines()]
    assert [(r[0], r[2], r[5]) for r in rows] == [("7", "Doing", "janitor"), ("8", "Closed", "reconcile")]


def test_an_unproven_write_keeps_its_pending_event_and_a_proven_one_loses_it(tmp_path):
    """A write the API never answered keeps its prediction (#1841): only the writer's own proof, `boardw`, ends it. A
    write the gates no longer back is dropped by name, and only a pending event can be dropped."""
    run(tmp_path, "board", "a/b#7", "janitor", "Doing", "", "1700000000", "janitor")
    assert run(tmp_path, "pending", "a/b#7").stdout.split("\t")[0] == "7", "an unproven write lost its pending event"
    assert run(tmp_path, "boardw", "a/b#7", "janitor", "Doing", "janitor").stdout.strip() == "written"
    assert run(tmp_path, "pending", "a/b#7").stdout == ""
    assert run(tmp_path, "boardx", "a/b#7", "janitor", "Doing").stdout.strip() == "superseded", "a written event was dropped"


def test_an_event_older_than_the_fact_the_ledger_holds_is_stale(tmp_path):
    """The older-instant rule decides which writer's fact wins (#1841): an eventually consistent read that returns an old
    value never overwrites a newer recorded write, and the newer fact stands with its own writer."""
    assert run(tmp_path, "board", "a/b#7", "janitor", "Doing", "", "2000", "janitor").stdout.strip() == "recorded"
    assert run(tmp_path, "board", "a/b#7", "reconcile", "Todo", "", "1000", "reconcile").stdout.strip() == "stale"
    fact = json.loads((tmp_path / "state" / "a__b__7.json").read_text())["board"]
    assert (fact["column"], fact["instant"], fact["by"]) == ("Doing", 2000.0, "janitor")
