"""#1514: two overlapping dispatch passes must not both pick the same issues and dispatch them twice.

Host-free: the real dispatchers run against stubs for gh, cluster.py, mkreq.sh and launch.py, with their own
SG_STATE, so nothing here touches the network, a model or the live loop.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "contrib" / "squad-goal" / "scripts"

GH = """#!/bin/bash
# A stub gh: one open issue (#42), no open PR. autobug reads `<number><US>labels>`; autofrontier sorts on
# `<wave><US><number><US><labels>`, so the two queries answer in their own shape. autobug asks by label;
# autofrontier reads every open issue, takes only one that carries SG_FRONTIER_LABEL (#485), and lets stack.sh's blocker gate decide.
case "$*" in *"pulls?state=open"*) exit 0;; esac
# autofrontier reads the open-issue list once, through the door: one GraphQL request (#383).
labels='{"nodes":[{"name":"wave:01"},{"name":"ready-for-agent"}]}'
case "$*" in *"issues(states: OPEN"*) printf '{"data":{"repository":{"issues":{"pageInfo":{"hasNextPage":false},"nodes":[{"number":42,"title":"a bug","body":"b","state":"OPEN","labels":%s,"blockedBy":{"nodes":[]},"comments":{"nodes":[]}}]}}}}\\n' "$labels"; exit 0;; esac
case "$*" in *"issues?state=open&labels="*) printf '%s' "$LABELS" | jq -Rc '[{number:42,title:"a bug",body:"b",state:"open",labels:(split(",")|map(select(length>0)|{name:.}))}]'; exit 0;; esac   # gh's JSON list: the door projects it (#847)
case "$*" in *"issues?state=open"*) printf 'wave:01\\03742\\037%s\\n' "$LABELS"; exit 0;; esac
case "$*" in *"issue(number: 42)"*) printf '{"data":{"repository":{"i42":{"number":42,"title":"a bug","body":"b","state":"OPEN","labels":{"nodes":[]},"blockedBy":{"nodes":[]},"comments":{"nodes":[]}}}}}\n'; exit 0;; esac   # the door's one-issue query (#847)
case "$*" in *"issues/42"*) printf '{"number":42,"title":"a bug","body":"b","created_at":"2026-10-11T00:00:00Z"}\\n'; exit 0;; esac
exit 0
"""

STARTABILITY = """. "$(dirname "${BASH_SOURCE[0]}")/ghcall.sh"   # as the real one: the aggregate read (gh_aggregate) comes through it
sg_not_startable() { return 1; }
sg_fork_branch() { echo "gui/$1"; }
sg_fork_branch_pattern() { echo "gui/%n"; }
sg_issue_worktree_note() { :; }
sg_dispatch_claimed() { [ -e "$SG_STATE/auto/bug-$1" ] || [ -e "$SG_STATE/auto/disp-$1" ]; }   # as the real one: the shared claim
sg_live_issues() { :; }   # active-runs.txt is empty here: no live run holds an issue
sg_board_rows() { printf '42\tTodo\n'; }   # the card of #42 sits in Todo, the owner's go signal (#550)
sg_todo_of() { awk -F '\t' -v t="${SG_TODO:-Todo}" '$2 == t { print $1 }'; }
"""

@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    """The live loop exports SG_SINGLE_<stage> to a run it dispatches; a leaked one lets a pass walk past the lock it must hold."""
    for k in [k for k in os.environ if k.startswith("SG_SINGLE_")]:
        monkeypatch.delenv(k)


STACK = """#!/bin/bash
echo "READY"
"""

LAUNCH = """import sys
sys.exit(0)
"""


def _scripts(tmp_path, cluster_sleep):
    """A scripts folder holding the real dispatchers and single.sh, plus every stub they call."""
    d = tmp_path / "scripts"
    d.mkdir(parents=True)
    for name in ("single.sh", "autobug.sh", "autofrontier.sh", "ghcall.sh", "ghread.py", "ghthrottle.py",
                 "refusal-names.sh", "workbranches.sh", "issue_ledger.py", "loopbug.sh", "basered.sh"):
        (d / name).write_text((SCRIPTS / name).read_text())
    (d / "env.sh").write_text("")                       # the test sets every SG_* it needs
    (d / "startability.sh").write_text(STARTABILITY)
    (d / "cluster.py").write_text(f"import time\ntime.sleep({cluster_sleep})\nprint('42')\n")
    (d / "stack.sh").write_text(STACK)
    (d / "launch.py").write_text(LAUNCH)
    # The stub mkreq.sh records whether the issue was claimed at the moment the request was built (#1514).
    (d / "mkreq.sh").write_text(
        '#!/bin/bash\nname=$1\n'
        'printf "marker=%s\\n" "$([ -e "$SG_STATE/auto/bug-42" ] || [ -e "$SG_STATE/auto/disp-42" ] '
        '&& echo present || echo absent)" > "$SG_STATE/mkreq-seen.txt"\n'
        'printf "%s\\n" "$SG_STATE/req/$name.txt"\n')
    for f in d.iterdir():
        f.chmod(0o755)
    gh = tmp_path / "bin" / "gh"
    gh.parent.mkdir()
    gh.write_text(GH)
    gh.chmod(0o755)
    return d, gh


def _state(tmp_path, script):
    """The SG_* a dispatcher needs: state, the checkout it reads, the board and the repo."""
    st = tmp_path / "state"
    (st / "auto").mkdir(parents=True)
    (st / "req").mkdir()
    (st / "cq").mkdir()
    (st / "active-runs.txt").write_text("")
    (tmp_path / "checkout").mkdir()
    labels = "wave:01,ready-for-agent" if script == "autofrontier.sh" else "bug,wave:01,ready-for-agent"
    return {
        # the python3 of this suite first: the dispatchers read the free slots through squad/caps.py, which needs PyYAML
        "PATH": f"{tmp_path / 'bin'}:{Path(sys.executable).parent}:/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": str(tmp_path / "home"),
        "SG_STATE": str(st),
        "SG_CHECKOUT": str(tmp_path / "checkout"),
        "SG_REPO": "o/n", "SG_OWNER": "o", "SG_PROJECT": "1", "SG_BUG_PROJECT": "",
        "SG_BASE": "main", "SG_BUG_LABEL": "bug", "SG_FRONTIER_LABEL": "ready-for-agent",
        "SG_FRONTIER_PREAMBLE": "", "BOARD": "true", "LABELS": labels,
        "SG_SQUAD": str(SCRIPTS.parents[2]), "SQUAD_CAPS_DIR": str(tmp_path / "caps"),   # the free slots come from squad/caps.py
        "SG_MIN_FREE_GB": "0",   # the disk floor reads the real volume (#615): a fixture pass must not stop on this machine's free space (#792)
    }


def _pass(d, env, tmp_path, label):
    return subprocess.Popen(["bash", str(d / label)], env=env, cwd=tmp_path,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def _bodies(tmp_path):
    return sorted(p.name for p in (tmp_path / "state" / "req").glob("*.body"))


@pytest.mark.heavy
def test_two_overlapping_autobug_passes_dispatch_once(tmp_path):
    """The second pass starts inside the first one's pick: it must see the claim and dispatch nothing."""
    d, _ = _scripts(tmp_path, cluster_sleep=3)
    env = {**os.environ, **_state(tmp_path, "autobug.sh")}
    (tmp_path / "home").mkdir()
    first = _pass(d, env, tmp_path, "autobug.sh")
    time.sleep(1.2)                                      # the first pass is inside its pick (cluster.py sleeps 3 s)
    second = _pass(d, env, tmp_path, "autobug.sh")
    out = [p.communicate(timeout=90)[0] for p in (first, second)]

    assert "AUTOBUG dispatched" in out[0], out[0]
    assert second.returncode == 0, out[1]
    assert "AUTOBUG dispatched" not in out[1], f"the overlapping pass dispatched a second time:\n{out[1]}"
    assert len(_bodies(tmp_path)) == 1, f"two requests were built: {_bodies(tmp_path)}"


@pytest.mark.heavy
def test_autobug_claims_the_issue_before_it_builds_the_request(tmp_path):
    """A crash between the pick and the launch must still leave the issue claimed (#1514)."""
    d, _ = _scripts(tmp_path, cluster_sleep=0)
    env = {**os.environ, **_state(tmp_path, "autobug.sh")}
    (tmp_path / "home").mkdir()
    out = _pass(d, env, tmp_path, "autobug.sh").communicate(timeout=90)[0]
    assert "AUTOBUG dispatched" in out, out

    seen = (tmp_path / "state" / "mkreq-seen.txt").read_text().strip()
    assert seen == "marker=present", f"mkreq.sh built the request with the issue unclaimed: {seen}"
    marker = tmp_path / "state" / "auto" / "bug-42"
    assert marker.exists() and marker.read_text().strip().startswith("autobug-"), marker.read_text()


@pytest.mark.heavy
def test_autofrontier_claims_the_issue_before_it_builds_the_request(tmp_path):
    """The same claim-before-build rule holds for the frontier dispatcher (#1514)."""
    d, _ = _scripts(tmp_path, cluster_sleep=0)
    env = {**os.environ, **_state(tmp_path, "autofrontier.sh")}
    (tmp_path / "home").mkdir()
    out = _pass(d, env, tmp_path, "autofrontier.sh").communicate(timeout=90)[0]
    assert "FRONTIER dispatched" in out, out

    seen = (tmp_path / "state" / "mkreq-seen.txt").read_text().strip()
    assert seen == "marker=present", f"mkreq.sh built the request with the issue unclaimed: {seen}"
