"""GitHub issue #1564: an id the add box offers has to be one the chain that will run it can use.

The box mixes three families -- the ids `codex exec` takes, the Antigravity ones `agy` takes, and the ids the
gateway serves under an alias. The gateway's own `-fast` tier is a spelling only the gateway route serves, and no
chain consumer goes through the gateway: review.sh hands a bare id to `codex exec` and a `claude:`-prefixed one to
`claude -p --model`. Folded onto the bare id the tier read as a codex model, so an entry picked from the box died
on every call -- `codex exec -m gpt-5.6-terra-fast` answers 400 "The 'gpt-5.6-terra-fast' model is not supported
when using Codex with a ChatGPT account" -- and the chain fell through to its next entry.
"""
import importlib.util
import json
import os
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"

# What the hub's own `/v1/models` carried on 2026-10-11 (the gateway's local catalogue), the `-fast` tier and its
# bare counterpart side by side. A gateway reachable in a test is not: the fixture hands this list over.
GATEWAY = ["claude-gpt-5.6-terra", "claude-gpt-5.6-terra-fast", "claude-gpt-5.6-sol", "claude-gpt-5.6-sol-fast",
           "claude-gpt-5.6-luna", "claude-gpt-5.6-luna-fast", "claude-grok-composer-2.5-fast"]
# ids a chain consumer answers, named from the panel's side: the fold's own spelling, and one the chains already use
RUNNABLE = ["gpt-5.6-terra", "gpt-5.6-sol", "gpt-5.6-luna"]
GATEWAY_ONLY = ["gpt-5.6-terra-fast", "gpt-5.6-sol-fast", "gpt-5.6-luna-fast", "grok-composer-2.5-fast"]

NO_CHAINS = {"coding_models": [], "review_models": [], "vision_models": []}


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_model_add_box", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def box(hub, monkeypatch, tmp_path):
    """The add box's own list, with both sources of gateway spellings pinned to what they held on 2026-10-11: the
    gateway's catalogue and the Claude Code settings, which registers the same wrapper aliases (the real one carries
    `claude-gpt-5.6-sol-fast` beside `claude-opus-5-5`)."""
    monkeypatch.setattr(hub.pathlib.Path, "home", classmethod(lambda cls: tmp_path))
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text(json.dumps(
        {"modelSettings": {"claude-gpt-5.6-sol-fast": {}, "claude-opus-5-5": {}}}))
    monkeypatch.setattr(hub, "gateway_models", lambda ttl=120: list(GATEWAY))
    return lambda d=None: hub.known_models(d or dict(NO_CHAINS))


def test_the_add_box_offers_no_id_only_the_gateway_route_serves(box):
    offered = box()
    assert [m for m in GATEWAY_ONLY if m in offered] == []


def test_the_add_box_still_offers_the_gateway_ids_a_chain_consumer_answers(box):
    offered = box()
    assert [m for m in RUNNABLE + ["claude-opus-5-5"] if m not in offered] == []   # claude-opus-5-5: from the settings


def test_one_model_is_still_one_row(box):
    offered = box({"coding_models": ["gpt-5.6-terra"], "review_models": ["gpt-5.6-terra"], "vision_models": []})
    assert offered.count("gpt-5.6-terra") == 1
