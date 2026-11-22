"""The hub is theme modules so two hub tickets on two themes dispatch together (#2742).

`hub.py` holds the imports, the theme loader, the server and its route table. Each Settings theme and each reader or page
builder lives in its own `hub_theme_<theme>.py`, compiled into the hub's one namespace in load order. The proof that the
split does what the issue asks is `mkreq.sh`: two requests that edit two different theme modules name no shared file, so
the dispatch guard passes both where it refused the second request while both named hub.py.
"""
import os
import shutil
import subprocess
from pathlib import Path

from tests.hub_source import themes

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "contrib/squad-goal/scripts"
HUB = SCRIPTS / "hub.py"
LIVE_GUARD = SCRIPTS / "live-guard.sh"

SETTINGS_THEMES = (
    "hub_theme_settings_limits",            # Spend & limits
    "hub_theme_settings_subscriptions",     # Subscriptions
    "hub_theme_settings_models",            # Models & chains
    "hub_theme_settings_communication",     # Communication
    "hub_theme_settings_loop",              # Loop switch
    "hub_theme_settings_access",            # Access
    "hub_theme_settings_board",             # Kanban columns and machine slots
)
THEME_A = "contrib/squad-goal/scripts/hub_theme_settings_models.py"
THEME_B = "contrib/squad-goal/scripts/hub_theme_settings_communication.py"


def test_each_settings_theme_lives_in_its_own_module():
    for name in SETTINGS_THEMES:
        assert (SCRIPTS / (name + ".py")).is_file(), f"{name}.py is missing"


def test_hub_py_keeps_the_server_the_route_table_and_the_imports():
    text = HUB.open(encoding="utf-8").read()
    for moved in ("def limits_set(", "def slack_files_post(", "def models_set(", "def loop_switch(", "def columns_set(",
                  "def scan(", "def board(", "PAGE = ", "def stats("):
        assert moved not in text, f"{moved} is still in hub.py"
    assert "class H(BaseHTTPRequestHandler):" in text
    assert 'elif self.path == "/api/slack/files": out = slack_files_post(body)' in text
    assert "def _load_theme(name):" in text


def test_every_theme_module_is_on_the_live_guard_publish_list():
    listed = LIVE_GUARD.read_text().split("hub_paths=(", 1)[1].split(")", 1)[0]
    assert "scripts/hub.py" in listed
    for name in themes():
        assert f"scripts/{name}.py" in listed, f"{name}.py is not published"


def test_the_hub_watches_every_theme_module():
    text = HUB.open(encoding="utf-8").read()
    assert "for name in _THEMES))" in text, "the watch set is not built from the theme manifest"
    assert "for p, stamp in WATCHED.items()" in text, "watch_self does not compare the theme files"


def _mkreq_tree(tmp_path):
    """A scratch copy of mkreq.sh and its data, as test-mkreq.sh builds one: no loop state, no fork checkout."""
    s = tmp_path / "scripts"
    s.mkdir()
    for f in ("mkreq.sh", "address.sh", "models.json", "req-checklist.txt", "modelreg.py", "registry.json"):
        shutil.copy(SCRIPTS / f, s / f)
    (s / "env.sh").write_text(f"export SG_REPO=o/r SG_CHECKOUT={tmp_path}/co\n")
    env = {k: v for k, v in os.environ.items() if not k.startswith("SG_")}
    env.update(HOME=str(tmp_path / "home"), SG_STATE=str(tmp_path / "state"))
    (tmp_path / "home").mkdir()
    (tmp_path / "state" / "req").mkdir(parents=True)
    return s, env


def _body(tmp_path, name, path):
    body = tmp_path / f"{name}.body"
    body.write_text(f"IMPORTANT: your first and only action is X\nPANEL: p1\nEdit {path}\n")
    return body


def _claim(scripts, body, env):
    out = subprocess.run(["bash", str(scripts / "mkreq.sh"), "--paths", str(body)], env=env, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return set(out.stdout.split())


def test_two_requests_that_edit_two_themes_pass_mkreq_at_the_same_time(tmp_path):
    scripts, env = _mkreq_tree(tmp_path)
    a = _body(tmp_path, "themes_a", THEME_A)
    b = _body(tmp_path, "themes_b", THEME_B)
    # The two slices name two files and share none: the split is what lets the second request launch.
    pa, pb = _claim(scripts, a, env), _claim(scripts, b, env)
    assert THEME_A in pa and THEME_B in pb, (pa, pb)
    assert not (pa & pb), f"the two theme requests share {pa & pb}"
    ra = subprocess.run(["bash", str(scripts / "mkreq.sh"), "themes_a", str(a)], env=env, capture_output=True, text=True)
    rb = subprocess.run(["bash", str(scripts / "mkreq.sh"), "themes_b", str(b)], env=env, capture_output=True, text=True)
    assert ra.returncode == 0, ra.stderr
    assert rb.returncode == 0, rb.stderr
    assert "refusing" not in (ra.stderr + rb.stderr), ra.stderr + rb.stderr
    assert (tmp_path / "state" / "req" / "themes_a.txt").is_file()
    assert (tmp_path / "state" / "req" / "themes_b.txt").is_file()


def test_two_requests_on_the_same_theme_still_refuse(tmp_path):
    scripts, env = _mkreq_tree(tmp_path)
    a = _body(tmp_path, "same_a", THEME_A)
    b = _body(tmp_path, "same_b", THEME_A)
    assert subprocess.run(["bash", str(scripts / "mkreq.sh"), "same_a", str(a)], env=env, capture_output=True).returncode == 0
    rb = subprocess.run(["bash", str(scripts / "mkreq.sh"), "same_b", str(b)], env=env, capture_output=True, text=True)
    assert rb.returncode != 0, "a second request on the same theme module launched"
    assert THEME_A in rb.stderr, rb.stderr
