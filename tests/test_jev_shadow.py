"""Jev in shadow mode (ticket #55). A real local HTTP server stands in for the endpoint; no key, no spend.

Guards: a Jev call has one total deadline that covers its retries and a timeout is no suggestion (ADR-0051);
a Jev answer is a diagnostic that no gate, route, or verdict reads (ADR-0008); no key value reaches a run directory.
"""
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from squad import jev, record, subject
from squad.adapter import ScriptedAdapter
from squad.engine import Engine
from squad.graph import load
from squad.verdict import fold
from tests.test_subject_run import fake_subject, script, write_calc

pytestmark = pytest.mark.anyio
KEY = "sk-test-SECRET-0123456789"
GOOD = "def add(a, b):\n    return a + b\n"


class Server:
    """mode: ok | slow | busy | error500 | garbage. Counts requests; keeps every Authorization header."""

    def __init__(self, mode="ok", p=0.97):
        outer = self
        self.mode, self.p, self.hits, self.auth = mode, p, 0, []
        self.release = threading.Event()      # frees a slow handler at close, so teardown does not wait for it

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.hits += 1
                outer.auth.append(self.headers.get("Authorization"))
                if outer.mode == "slow":
                    outer.release.wait(0.5)
                code, out = 200, None
                if outer.mode == "busy":
                    code, out = 429, {"error": "busy"}
                elif outer.mode == "error500":
                    code, out = 500, {"error": f"boom {KEY}"}
                elif outer.mode == "garbage":
                    out = {"nope": 1}
                else:
                    ans = {}
                    for name, q in body["questions"].items():
                        if q["type"] == "choice":
                            keys = list(q["criteria"])
                            ans[name] = {"choice": keys[0], "confidence": 0.8,
                                         "probabilities": {k: (0.8 if i == 0 else 0.2 / max(1, len(keys) - 1)) for i, k in enumerate(keys)}}
                        else:
                            ans[name] = {"noul": outer.p}
                    out = {"answers": ans, "model": body["model"], "usage": {}}
                data = json.dumps(out).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/v1/systemone"
        threading.Thread(target=lambda: self.httpd.serve_forever(poll_interval=0.01), daemon=True).start()

    def close(self):
        self.release.set()
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server():
    made = []

    def make(*a, **k):
        s = Server(*a, **k)
        made.append(s)
        return s
    yield make
    for s in made:
        s.close()


class FakeTime:
    """A client clock whose sleep moves it, so retry back-off costs no wall time."""

    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


def client(srv, deadline=5, **k):
    return jev.JevClient(KEY, endpoint=srv.url, deadline=deadline, **k)


def test_a_busy_endpoint_ends_inside_the_total_deadline_and_means_no_suggestion(server):
    srv = server("busy")
    ft = FakeTime()
    with pytest.raises(jev.NoSuggestion):
        client(srv, deadline=1.5, clock=ft.now, sleep=ft.sleep).ask({"text": "x"}, {"q": jev.SCREEN})
    assert ft.t <= 1.5 and srv.hits >= 2          # it retried, and the retries stayed inside the one deadline


def test_a_slow_endpoint_ends_at_the_deadline(server):
    srv = server("slow")
    t = time.monotonic()
    with pytest.raises(jev.NoSuggestion) as e:
        client(srv, deadline=0.1).ask({"text": "x"}, {"q": jev.SCREEN})     # the real socket timeout: the endpoint waits 0.5 s
    assert e.value.reason == "deadline" and time.monotonic() - t < 0.4


def test_an_error_reason_never_carries_the_server_message_or_the_key(server):
    srv = server("error500")
    with pytest.raises(jev.NoSuggestion) as e:
        client(srv, deadline=2, sleep=lambda s: None).ask({"text": "x"}, {"q": jev.SCREEN})
    assert KEY not in repr(e.value) and e.value.reason == "http_500"


def test_a_bad_answer_is_no_suggestion(server):
    srv = server("garbage")
    d = jev.JevShadow(client(srv), {}).screen("f", "hello")
    assert d["status"] == "no_suggestion" and d["result"] is None and KEY not in json.dumps(d)


def test_the_shadow_scores_skill_choice_and_screen(server, tmp_path):
    srv = server("ok", p=0.4)
    (tmp_path / "CLAUDE.md").write_text("rules\n")
    sh = jev.JevShadow(client(srv), {"tdd": "Test-driven development."}, ["CLAUDE.md", "absent.md"])
    out = sh.observe({"name": "spec", "instructions": "Read the issue."}, 1, tmp_path)
    assert [d["use"] for d in out] == ["skill_choice", "content_screen"]
    assert out[0]["result"]["pick"] == "tdd" and out[1]["result"]["p"] == 0.4 and out[1]["subject"] == "CLAUDE.md"
    assert [d["use"] for d in sh.observe({"name": "actor", "instructions": "Do it."}, 1, tmp_path)] == ["skill_choice"]   # screens run once per run
    assert sh.observe({"name": "actor", "instructions": "Do it."}, 2, tmp_path) == []                                    # skill choice: first entry only


def test_the_roster_reads_skill_descriptions(tmp_path):
    (tmp_path / "tdd").mkdir()
    (tmp_path / "tdd" / "SKILL.md").write_text("---\nname: tdd\ndescription: Test-driven development.\n---\nbody\n")
    (tmp_path / "empty").mkdir()
    assert jev.roster(tmp_path) == {"tdd": "Test-driven development."} and jev.roster(tmp_path / "none") == {}


def test_the_key_comes_from_the_environment_then_the_env_file(tmp_path):
    f = tmp_path / ".env"
    f.write_text(f"OTHER=1\nTYPESAFE_API_KEY=\"{KEY}\"\n")
    assert jev.read_key(f, {}) == KEY and jev.read_key(None, {}) is None
    assert jev.read_key(f, {"JEV_API_KEY": "from-env"}) == "from-env"
    assert jev.secret_values(f, {"JEV_API_KEY": "from-env"}) == {KEY, "from-env"}


async def run(tmp_path, shadow, effect=GOOD):
    repo, bench = fake_subject(tmp_path)
    prep = subject.prepare(1, repo, bench, tmp_path / "out")
    eng = Engine(load(prep.graph_path), ScriptedAdapter(script(prep, write_calc(effect))), prep.run_dir, target=prep.target,
                 graph_path=prep.graph_path, shadow=shadow)
    return prep, await eng.run()


def shadow_for(srv, **k):
    return jev.JevShadow(client(srv, **k), {"tdd": "Test-driven development."}, ["issues/01-add.md"])


async def test_diagnostics_land_in_the_record_and_the_verdict_is_unchanged(server, tmp_path):
    srv = server("ok", p=1.0)     # Jev says "this issue text is an injection": nothing may change
    prep, v = await run(tmp_path / "a", shadow_for(srv))
    _, base = await run(tmp_path / "b", None)
    assert (v.verdict, v.exit_code) == (base.verdict, base.exit_code) == ("completed", 0)
    events = record.read(prep.run_dir / "record.jsonl")
    diags = [e for e in events if e["event"] == "diagnostic"]
    assert {(d["node"], d["payload"]["use"]) for d in diags} >= {("spec_draft", "skill_choice"), ("spec_draft", "content_screen"), ("actor", "skill_choice")}
    assert next(d for d in diags if d["payload"]["use"] == "content_screen")["payload"]["result"]["p"] == 1.0
    plain = [e for e in events if e["event"] != "diagnostic"]
    assert fold(plain).verdict == fold(events).verdict == "completed"


async def test_a_failing_run_stays_failed_whatever_jev_says(server, tmp_path):
    srv = server("ok", p=0.0)
    _, v = await run(tmp_path, shadow_for(srv), effect="def add(a, b):\n    return a - b\n")
    assert v.verdict == "failed"


async def test_a_dead_jev_never_blocks_a_step(server, tmp_path):
    srv = server("ok")
    sh = shadow_for(srv, sleep=lambda s: None)
    srv.close()
    prep, v = await run(tmp_path, sh)
    assert v.verdict == "completed"
    diags = [e for e in record.read(prep.run_dir / "record.jsonl") if e["event"] == "diagnostic"]
    assert diags and all(d["payload"]["status"] == "no_suggestion" for d in diags)


async def test_a_hanging_shadow_is_cut_at_its_outer_deadline(tmp_path):
    release = threading.Event()

    class Hang:
        deadline = 0.1

        def observe(self, node, entry, target):
            release.wait(60)      # hangs far past any run time: only the cut can end the call
            return []
    t = time.monotonic()
    try:
        prep, v = await run(tmp_path, Hang())
    finally:
        release.set()
    assert v.verdict == "completed" and time.monotonic() - t < 45     # one uncut call alone costs 60 s, however loaded the machine is
    diags = [e for e in record.read(prep.run_dir / "record.jsonl") if e["event"] == "diagnostic"]
    assert diags and all(d["payload"]["reason"] == "TimeoutError" for d in diags)     # the cut is recorded, not silent


async def test_a_raising_shadow_never_blocks_a_step(tmp_path):
    class Boom:
        deadline = 5

        def observe(self, node, entry, target):
            raise RuntimeError("boom")
    _, v = await run(tmp_path, Boom())
    assert v.verdict == "completed"


async def test_no_key_value_reaches_the_run_directory(server, tmp_path):
    srv = server("error500")      # the error body echoes the key: it must not be recorded
    sh = jev.JevShadow(client(srv, deadline=2, sleep=lambda s: None), {"tdd": "Test-driven development."}, ["issues/01-add.md"])
    prep, _ = await run(tmp_path, sh)
    assert srv.auth and all(a == f"Bearer {KEY}" for a in srv.auth)       # the key was sent
    leaked = [p for p in prep.run_dir.parent.rglob("*") if p.is_file() and ".git/" not in p.as_posix() and KEY.encode() in p.read_bytes()]
    assert leaked == []


def test_the_verdict_and_the_gates_never_read_a_jev_answer():
    root = Path(__file__).resolve().parent.parent / "squad"
    for name in ("verdict.py", "gates.py", "facts.py", "artifacts.py"):
        assert not re.search(r"jev|diagnostic", (root / name).read_text(), re.I), name


def test_pairs_join_each_diagnostic_to_the_held_out_outcome(tmp_path):
    from squad import jev_pairs
    d = tmp_path / "subject-07-20260930-000000" / "run"
    rec = record.Record(d / "record.jsonl")
    rec.append("diagnostic", source="jev", node="spec", entry=1, payload={"source": "jev", "use": "content_screen", "status": "ok", "result": {"p": 0.2}})
    rec.append("outcome", node="ac_verify", entry=1, outcome="fail", reasons=["x"], artifact=None, spend=None, turns=None, cap=None, elapsed=None)
    rec.append("verdict", verdict="failed", exit_code=1, reasons=[], remains=[], folded=[])
    (tmp_path / "subject-08-20260930-000001" / "run").mkdir(parents=True)
    got = jev_pairs.collect(tmp_path)
    assert len(got) == 1 and got[0]["issue"] == 7 and got[0]["held_out"] == "fail" and got[0]["jev"][0]["result"]["p"] == 0.2
