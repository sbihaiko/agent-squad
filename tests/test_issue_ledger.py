"""One ledger file per issue holds the loop's facts about it (#673): attempts per run, the last failure, the stop reason.

issue_ledger.py is the only writer of $SG_STATE/state/<owner>__<repo>__<n>.json. A write is atomic (ADR-0030), the key names
the repository (ADR-0138), a missing file of a new issue reads as todo with 0 attempts, and a missing file beside the old
requeues-N files holds the issue until `migrate` carries them in (ADR-0149 rule 4).
"""
import json
import os
import subprocess
import sys
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
