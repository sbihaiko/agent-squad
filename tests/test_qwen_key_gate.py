"""Qwen 3.8 Flash runs only when an OpenCode Go key exists (#482).

The bridge serves `qwen3.8-flash` through its OpenCode Go backend, which refuses every call with no key. The owner's ruling:
with no key the model is disabled -- never offered in the hub, never named by a request, never left in a run's chain -- and
nothing fails because of it. With a key (`opencode.apiKey` in the bridge config, or CCP_OPENCODE_API_KEY) it is enabled.
Every case points the config at a temporary file and clears the env var, so the machine's own config is never read; the
fake key is a placeholder, never a credential.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from tests.gitrepo import with_origin

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "contrib/squad-goal/scripts"
QWEN = "claude-qwen3.8-flash"
FAKE = "placeholder-not-a-key"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def no_key(tmp_path, monkeypatch):
    cfg = tmp_path / "ccp.json"
    cfg.write_text(json.dumps({"opencode": {}, "grok": {"apiKey": FAKE}}))
    monkeypatch.setenv("SG_CCP_CONFIG", str(cfg))
    monkeypatch.delenv("CCP_OPENCODE_API_KEY", raising=False)
    return cfg


@pytest.fixture
def with_key(no_key):
    no_key.write_text(json.dumps({"opencode": {"apiKey": FAKE}}))
    return no_key


def test_without_a_key_qwen_is_closed_and_no_other_model_is(no_key):
    pk = _load("provkey_a", SCRIPTS / "provkey.py")
    assert pk.closed(QWEN) and pk.closed("qwen3.8-flash") and pk.closed("claude:claude-qwen3.8-flash")
    assert pk.reason(QWEN) == "no OpenCode Go key"
    assert not any(pk.closed(m) for m in ("claude-deepseek-flash[1m]", "claude-grok-4.7", "gpt-5.6-terra", "claude-sonnet-5-5"))


def test_an_empty_key_is_no_key(no_key):
    no_key.write_text(json.dumps({"opencode": {"apiKey": "  "}}))
    assert _load("provkey_b", SCRIPTS / "provkey.py").closed(QWEN)


def test_a_key_in_the_config_opens_qwen(with_key):
    pk = _load("provkey_c", SCRIPTS / "provkey.py")
    assert not pk.closed(QWEN) and pk.reason(QWEN) is None


def test_a_key_in_the_env_opens_qwen(no_key, monkeypatch):
    monkeypatch.setenv("CCP_OPENCODE_API_KEY", FAKE)
    assert not _load("provkey_d", SCRIPTS / "provkey.py").closed(QWEN)


@pytest.fixture
def hub(monkeypatch):
    for k, v in (("SG_REPO", "x/y"), ("SG_OWNER", "x"), ("SG_PROJECT", "4")):
        monkeypatch.setenv(k, os.environ.get(k, v))
    monkeypatch.setenv("SG_HUB_DIR", tempfile.mkdtemp())
    monkeypatch.syspath_prepend(str(SCRIPTS))
    mod = _load("hub_qwen_key", SCRIPTS / "hub.py")
    monkeypatch.setattr(mod, "gateway_models", lambda ttl=120: ["claude-grok-4.7", QWEN])
    return mod


CHAINS = {"coding_models": [QWEN, "claude-grok-4.7"], "review_models": [], "vision_models": []}


def test_the_hub_never_offers_qwen_without_a_key(hub, no_key):
    assert "qwen3.8-flash" not in hub.known_models(CHAINS) and QWEN not in hub.known_models(CHAINS)
    assert QWEN not in hub.router_known()
    assert not hub.may_run(CHAINS, "coding_models", QWEN)
    assert hub.may_run(CHAINS, "coding_models", "claude-grok-4.7")


def test_the_hub_offers_qwen_with_a_key(hub, with_key):
    assert QWEN in hub.known_models(CHAINS)
    assert QWEN in hub.router_known()
    assert hub.may_run(CHAINS, "coding_models", QWEN)


def test_chainhealth_names_the_missing_key(no_key, tmp_path):
    ch = _load("chainhealth_qwen", SCRIPTS / "chainhealth.py")
    s = ch.chain_state({"review_models": ["claude:" + QWEN, "grok-4.7"]}, "review_models", str(tmp_path))
    assert s["available"] == ["grok-4.7"]
    assert s["out"][0]["reason"] == "no OpenCode Go key"


def _mkreq(tmp_path, cfg, env_key=None):
    s, home = tmp_path / "s", tmp_path / "home"
    s.mkdir(); home.mkdir()
    for f in ("mkreq.sh", "address.sh", "req-checklist.txt", "provkey.py", "modelreg.py", "registry.json"):
        shutil.copy(SCRIPTS / f, s / f)
    (s / "models.json").write_text(json.dumps({"coding_models": [QWEN, "test-model"]}))
    (tmp_path / "b.body").write_text("IMPORTANT: your first and only action is X\nPANEL: p\nEdit scripts/a.py\n")
    e = {k: v for k, v in os.environ.items() if not k.startswith(("SG_", "CCP_"))}
    e.update(HOME=str(home), SG_STATE=str(tmp_path / "st"), SG_SHARED=str(tmp_path / "sh"), SG_CCP_CONFIG=str(cfg))
    if env_key:
        e["CCP_OPENCODE_API_KEY"] = env_key
    r = subprocess.run(["bash", str(s / "mkreq.sh"), "b", str(tmp_path / "b.body")], env=e, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return next(line for line in (tmp_path / "st/req/b.txt").read_text().splitlines() if line.startswith("Coding model:"))


def test_a_request_never_names_qwen_without_a_key(no_key, tmp_path):
    line = _mkreq(tmp_path, no_key)
    assert line.startswith("Coding model: test-model ") and "qwen" not in line


def test_a_request_names_qwen_with_a_key(with_key, tmp_path):
    assert _mkreq(tmp_path, with_key).startswith(f"Coding model: {QWEN} ")


def _launch(tmp_path, cfg, coding_model):
    s, co, st, bin_ = tmp_path / "s", tmp_path / "co", tmp_path / "st", tmp_path / "bin"
    for d in (s, st, bin_, co / "runs", co / "docs/squad/workflows", tmp_path / "squad"):
        d.mkdir(parents=True, exist_ok=True)
    for f in ("launch.py", "router_envelope.py", "provkey.py", "modelreg.py", "registry.json"):
        shutil.copy(SCRIPTS / f, s / f)
    (s / "models.json").write_text(json.dumps({"coding_models": [QWEN, "test-model"], "review_models": [], "vision_models": []}))
    (s / "hub.py").write_text("print('hub')\n")
    doc = {"schema_version": 1, "name": "g", "root": "main", "nodes": [
        {"name": "main", "kind": "loop", "max": {"value": 1, "min": 1}, "children": ["route"]},
        {"name": "route", "kind": "dispatch", "entry": "router", "allow": {"models": ["test-model", QWEN], "efforts": ["low"]}},
        {"name": "router", "kind": "agent", "model": "test-model", "tools": ["Read"]}]}
    (co / "docs/squad/workflows/default.graph.json").write_text(json.dumps(doc))
    # the stub squad: record its argv, so the case reads the --spent-models the launcher hands the run; the hub start that
    # launch.py makes after the run, on a free port 7700, is not the squad and is not recorded
    (bin_ / "caffeinate").write_text(f"#!/bin/bash\ncase \"$*\" in *\" squad start \"*) echo \"$@\" > {tmp_path}/argv; mkdir -p runs/run-20261013-000000-aaaaaa;; esac\nexit 0\n")
    (bin_ / "uv").write_text("#!/bin/bash\necho uv-not-used\n")
    for p in (bin_ / "caffeinate", bin_ / "uv"):
        p.chmod(0o755)
    req = tmp_path / "req.txt"
    req.write_text(f"Coding model: {coding_model}\n--- issue #482\n")
    e = {k: v for k, v in os.environ.items() if not k.startswith(("SG_", "CCP_"))}
    e.update(PATH=f"{bin_}{os.pathsep}{os.environ['PATH']}", SG_STATE=str(st), SG_CHECKOUT=str(co), SG_SQUAD=str(tmp_path / "squad"),
             SG_SHARED=str(tmp_path / "sh"), SG_CCP_CONFIG=str(cfg))
    with_origin(co)   # launch.py fetches origin/main before a run (#580)
    r = subprocess.run([sys.executable, "-I", str(s / "launch.py"), str(req)], cwd=co, env=e, capture_output=True, text=True, timeout=60)
    argv = tmp_path / "argv"
    return r, argv.read_text() if argv.exists() else ""


@pytest.mark.heavy
def test_launch_never_chooses_qwen_without_a_key(no_key, tmp_path):
    r, _ = _launch(tmp_path, no_key, QWEN)
    assert r.returncode != 0 and "'test-model'" in r.stderr + r.stdout
    r, argv = _launch(tmp_path / "b", no_key, "test-model")
    assert r.returncode == 0, r.stderr
    assert f"--spent-models {QWEN}" in argv
    # a closed model is no noise: no warning names it (the run worktree path holds the test name, so it is cut first)
    assert "qwen" not in r.stderr.replace(str(tmp_path), "").lower()


@pytest.mark.heavy
def test_launch_chooses_qwen_with_a_key(with_key, tmp_path):
    r, argv = _launch(tmp_path, with_key, QWEN)
    assert r.returncode == 0, r.stderr
    assert "--spent-models" not in argv
