"""Guard: issue #2961. `voiced` checks each project's Slack revision against the registry of that project's own profile.

The hub records every save in the state directory of the profile it saved for, so one `slack-saved.json` per project.
`voiced` must read the file of the project it checks: a save for one project never approves another project's revision.
The seam under test is the daemon's own `refresh_slack` pass, wired as `main` wires it: each project is a git checkout
whose `origin/dev` holds the Slack file, and each registry is a real file outside every checkout. No case reaches Slack
or the network.
"""
import json
import sys
from pathlib import Path

from tests.test_voiced import OWNER, SLACK_VALID, FakeTranscriber, git, git_project, voiced

ROOT = Path(__file__).resolve().parent.parent
VOICE_DIR = ROOT / "contrib" / "voice"
if str(VOICE_DIR) not in sys.path:
    sys.path.insert(0, str(VOICE_DIR))


def slack_file(channel, name, language):
    """One valid committed file for a project of its own: its channel id, its channel name and its language."""
    return (SLACK_VALID.replace("C0PROJ0001", channel)
            .replace("proj-acme-app", name)
            .replace("language: en-US", f"language: {language}"))


def write_registry(state, *shas):
    """The registry the hub of one profile writes: `slack-saved.json` in that profile's state directory (#2694)."""
    state.mkdir(parents=True, exist_ok=True)
    path = state / "slack-saved.json"
    data = {f"/hub/worktrees/{sha}/docs/squad/slack.yaml":
            {"sha": sha, "at": "2026-11-25T00:00:00-03:00", "committed": True} for sha in shas}
    path.write_text(json.dumps(data))
    return path


def project_entry(repo, name, state):
    """One entry as the hub seam names it: the repository, its checkout and the state directory of its own profile."""
    return {"repo": name, "checkout": str(repo), "local": None, "state": str(state)}


def daemon_over(tmp_path, mod):
    """The daemon wired as `main` wires it: the per-project registry resolution of the profile seam."""
    return mod.Daemon(state=mod.State(str(tmp_path / "voice-state")), owner=OWNER,
                      transcriber=FakeTranscriber(), downloader=lambda url: b"",
                      log=lambda line: None, registry=mod.project_registry)


def two_projects(tmp_path):
    """Two checkouts, each with its own committed file, and the shas of their dev tips."""
    alpha = git_project(tmp_path, slack_file("C0ALPHA001", "proj-alpha", "pt-BR"), name="alpha")
    beta = git_project(tmp_path, slack_file("C0BETA0001", "proj-beta", "es-ES"), name="beta")
    return alpha, beta, git(alpha, "rev-parse", "refs/remotes/origin/dev"), git(beta, "rev-parse", "refs/remotes/origin/dev")


def test_a_save_in_one_projects_registry_approves_that_project_and_no_other(tmp_path):
    """Criteria (1) and (3): the save sits in alpha's own registry, and beta's registry lists nothing.

    Beta's sha is written in alpha's registry on purpose: a sha one project's registry lists must never approve another
    project's revision, so beta stays refused while alpha becomes active."""
    alpha, beta, sha_a, sha_b = two_projects(tmp_path)
    state_a, state_b = tmp_path / "state-alpha", tmp_path / "state-beta"
    write_registry(state_a, sha_a, sha_b)
    write_registry(state_b)
    mod = voiced()
    daemon = daemon_over(tmp_path, mod)

    daemon.refresh_slack(projects=[project_entry(alpha, "acme/alpha", state_a),
                                   project_entry(beta, "acme/beta", state_b)])

    active_a = daemon.state.active_revision("acme/alpha")
    assert active_a is not None and active_a["sha"] == sha_a
    assert daemon.state.active_revision("acme/beta") is None, "alpha's registry approved beta's revision"


def test_a_save_reaches_its_project_at_the_next_pass(tmp_path):
    """Criterion (2): a registry that lists nothing holds the project back, and the hub's save activates it next pass."""
    alpha, _, sha_a, _ = two_projects(tmp_path)
    state_a = tmp_path / "state-alpha"
    write_registry(state_a)                       # the profile names its registry, and no save is recorded yet
    mod = voiced()
    daemon = daemon_over(tmp_path, mod)
    projects = [project_entry(alpha, "acme/alpha", state_a)]

    daemon.refresh_slack(projects=projects)
    assert daemon.state.active_revision("acme/alpha") is None

    write_registry(state_a, sha_a)                # the hub saves the project's file, in this project's own registry
    daemon.refresh_slack(projects=projects)

    active = daemon.state.active_revision("acme/alpha")
    assert active is not None and active["sha"] == sha_a
    assert active["revision"]["project"]["language"] == "pt-BR"


def test_the_registry_of_one_profile_is_the_file_of_its_own_state(tmp_path):
    """Criterion (1): one entry names one registry, and the explicit environment name still wins for every project."""
    mod = voiced()
    assert mod.project_registry({"repo": "acme/app", "state": "/a"}, {}) == "/a/slack-saved.json"
    assert mod.project_registry({"repo": "acme/app", "state": "/b"}, {}) == "/b/slack-saved.json"
    assert mod.project_registry({"repo": "acme/app"}, {"SG_SLACK_REGISTRY": "/x/reg.json", "SG_STATE": "/a"}) == "/x/reg.json"
    assert mod.project_registry({"repo": "acme/app"}, {}) == ""


def test_the_seam_main_hands_the_daemon_is_none_when_no_registry_is_named(tmp_path):
    """The saved-sha check stays out of force on a machine that names no registry (#2695, #2961).

    The check belongs to a project whose own profile names a state directory, or to the explicit environment name that
    covers every project. A machine that names neither keeps the older behaviour, so `main` hands the daemon None."""
    mod = voiced()
    entry = {"repo": "acme/alpha", "checkout": str(tmp_path)}
    assert mod.hub_registry_seam([entry], {}) is None
    assert mod.hub_registry_seam([], {}) is None
    with_state = {"repo": "acme/alpha", "checkout": str(tmp_path), "state": str(tmp_path / "state-alpha")}
    assert mod.hub_registry_seam([with_state], {}) is mod.project_registry
    assert mod.hub_registry_seam([entry], {"SG_SLACK_REGISTRY": "/x/reg.json"}) is mod.project_registry


def test_a_machine_that_names_no_registry_serves_its_project(tmp_path):
    """A daemon whose seam is None activates the dev tip, because no save is required on that machine (#2961)."""
    alpha, _, sha_a, _ = two_projects(tmp_path)
    entry = {"repo": "acme/alpha", "checkout": str(alpha), "local": None}
    mod = voiced()
    daemon = mod.Daemon(state=mod.State(str(tmp_path / "voice-state")), owner=OWNER,
                        transcriber=FakeTranscriber(), downloader=lambda url: b"", log=lambda line: None,
                        registry=mod.hub_registry_seam([entry], {}))

    daemon.refresh_slack(projects=[entry])

    active = daemon.state.active_revision("acme/alpha")
    assert active is not None and active["sha"] == sha_a


def test_the_listing_covers_every_project_the_hub_names_not_only_this_pass(tmp_path):
    """A pass over one project still lists every project's registry, so no project's check falls out of force (#2961)."""
    alpha, beta, sha_a, _ = two_projects(tmp_path)
    state_a, state_b = tmp_path / "state-alpha", tmp_path / "state-beta"
    write_registry(state_a, sha_a)
    write_registry(state_b)
    entries = [project_entry(alpha, "acme/alpha", state_a), project_entry(beta, "acme/beta", state_b)]
    mod = voiced()
    daemon = mod.Daemon(state=mod.State(str(tmp_path / "voice-state")), owner=OWNER,
                        transcriber=FakeTranscriber(), downloader=lambda url: b"", log=lambda line: None,
                        configs=lambda: entries, registry=mod.project_registry)

    listed = daemon.listed(projects=[entries[0]])

    assert listed.get("acme/alpha") == {sha_a}, listed
    assert listed.get("acme/beta") == set(), listed


def test_a_channel_outside_this_pass_still_reads_its_own_registry(tmp_path):
    """A repository only the channel registry names still gets its registry read, so its check never falls out (#2961)."""
    alpha, _, _, sha_b = two_projects(tmp_path)
    reg_a, reg_b = tmp_path / "reg-alpha.json", tmp_path / "reg-beta.json"
    reg_a.write_text(json.dumps({}))
    reg_b.write_text(json.dumps({"/hub/x/docs/squad/slack.yaml":
                                 {"sha": sha_b, "at": "2026-11-25T00:00:00-03:00", "committed": True}}))
    paths = {"acme/alpha": str(reg_a), "acme/beta": str(reg_b)}
    mod = voiced()
    state = mod.State(str(tmp_path / "voice-state"))
    state.save_channels({"acme/beta": {"name": "proj-beta", "id": "C0BETA0001"}})
    daemon = mod.Daemon(state=state, owner=OWNER, transcriber=FakeTranscriber(), downloader=lambda url: b"",
                        log=lambda line: None, registry=lambda entry: paths.get(entry.get("repo") or "", ""))

    listed = daemon.listed(projects=[{"repo": "acme/alpha", "checkout": str(alpha)}])

    assert listed.get("acme/beta") == {sha_b}, listed
