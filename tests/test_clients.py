"""GitHub issue #2827 and #2922: the rules of the local client map (SPEC 2682).

The client map groups the projects of one customer across profiles, and the tree draws it as Client > Project. A project
counts as a project with Slack only when it declares a workspace or a persona. A project no client names is drawn
nowhere (#2922): it waits in every client's Add project list, and the tree holds no synthetic group.

The seam under test is the client map module itself: `normalize`, `groups`, `free_projects`, `has_slack`, and the four
writes of the map plus the client id a new name takes.
"""
from squad import clients as CL


def test_the_tree_draws_a_project_only_under_a_client_that_names_it():
    """#2922: a project no client names is in no group, so the tree draws it nowhere; `free_projects` names it for the
    Add project list. Two clients never share one project node."""
    doc = {"clients": [{"id": "one", "name": "One", "projects": ["alpha"]},
                       {"id": "two", "name": "Two", "projects": ["alpha", "beta"]}]}
    clients = CL.normalize(doc)
    groups = CL.groups(clients, ["alpha", "beta", "ghost"])
    assert [g["id"] for g in groups] == ["one", "two"], groups
    assert [p["id"] for p in groups[0]["projects"]] == ["alpha"], groups[0]
    assert [p["id"] for p in groups[1]["projects"]] == ["beta"], groups[1]
    assert CL.free_projects(clients, ["alpha", "beta", "ghost"]) == ["ghost"], "only the unnamed project waits"


def test_a_map_client_keeps_its_own_id_and_no_synthetic_node_appears():
    """#2922: the tree holds the map's clients alone. A map of one client draws one client, whatever the id."""
    groups = CL.groups(CL.normalize({"clients": [{"id": "one", "name": "One", "projects": ["alpha"]}]}), ["alpha", "beta"])
    assert [g["name"] for g in groups] == ["One"], groups
    assert CL.groups(CL.normalize({}), ["alpha"]) == [], "an empty map draws no client"


def test_a_project_counts_as_with_slack_only_with_a_workspace_or_a_persona():
    """A file that names only a version, a project block or a role is the project with no Slack; a workspace or a
    persona makes it a project with Slack, in either file."""
    assert CL.has_slack({"version": 1, "project": {"persona": "blue"}, "roles": [{"id": "m", "actions": ["status"]}]},
                        {}) is False
    assert CL.has_slack({"version": 1, "workspaces": [], "personas": []}, {}) is False
    assert CL.has_slack({"version": 1, "workspaces": [{"id": "T0TEAM0001"}]}, {}) is True
    assert CL.has_slack({}, {"version": 1, "personas": [{"id": "blue"}]}) is True


def test_adding_a_project_appends_it_as_a_follower_and_keeps_every_other_key():
    """#2922 acceptance 4 and 8: the project joins the client as a follower of its default persona, and the raw map
    keeps the form of every other row."""
    doc = {"version": 1, "clients": [{"id": "one", "name": "One", "default_persona": {"id": "blue", "name": "Blue"},
                                      "projects": [{"id": "alpha", "follows": False}]}]}
    after = CL.with_project(doc, "one", "beta")
    assert [p["id"] for p in CL.normalize(after)[0]["projects"]] == ["alpha", "beta"], after
    assert CL.normalize(after)[0]["projects"][1]["follows"] is True, "an added project follows the default"
    assert after["clients"][0]["projects"][0] == {"id": "alpha", "follows": False}, after
    assert CL.with_project(after, "one", "beta") == after, "a project the client already holds is added once"
    assert CL.with_project(doc, "nope", "beta") == doc, "a client the map does not name is never made"


def test_removing_a_project_leaves_every_other_row_as_it_stands():
    """#2922 acceptance 7 and 8: the project leaves the client, and the rest of the map keeps its place."""
    doc = {"version": 1, "clients": [
        {"id": "one", "name": "One", "projects": ["alpha", {"id": "beta", "follows": False}, "gamma"]},
        {"id": "two", "name": "Two", "projects": ["beta"]}]}
    after = CL.without_project(doc, "one", "beta")
    assert [p["id"] for p in CL.normalize(after)[0]["projects"]] == ["alpha", "gamma"], after
    assert after["clients"][1]["projects"] == ["beta"], "another client's row is untouched"
    assert CL.without_project(doc, "one", "ghost") == doc, "a project the client does not hold changes nothing"


def test_a_new_client_is_added_with_no_project_and_its_default_persona():
    """#2922 acceptance 10: an added client shows in the tree with no project."""
    doc = {"version": 1, "clients": [{"id": "one", "name": "One", "projects": ["alpha"]}]}
    after = CL.with_client(doc, "two", "Two", "blue", "Blue")
    assert [c["name"] for c in CL.normalize(after)] == ["One", "Two"], after
    two = CL.normalize(after)[1]
    assert two["projects"] == [], two
    assert two["default_persona"] == {"id": "blue", "name": "Blue"}, two
    assert CL.with_client(after, "two", "Other") == after, "a client id the map holds is never made twice"
    fresh = CL.with_client({}, "one", "One")
    assert CL.normalize(fresh) == [{"id": "one", "name": "One", "default_persona": None, "projects": []}], fresh


def test_a_deleted_client_leaves_the_map_and_its_projects_become_free():
    """#2922 acceptance 13: the client leaves the map, and the projects it held join the Add project list."""
    doc = {"clients": [{"id": "one", "name": "One", "projects": ["alpha", "beta"]},
                       {"id": "two", "name": "Two", "projects": []}]}
    after = CL.without_client(doc, "one")
    assert [c["id"] for c in CL.normalize(after)] == ["two"], after
    assert after["clients"] == [{"id": "two", "name": "Two", "projects": []}], after
    assert CL.free_projects(CL.normalize(after), ["alpha", "beta"]) == ["alpha", "beta"], after


def test_a_new_client_id_is_a_slug_of_the_name_and_never_a_taken_one():
    """#2922 acceptance 9: the control asks for a name alone, so the hub makes the id from it and keeps it unique."""
    one = CL.normalize({"clients": [{"id": "one", "name": "One"}]})
    assert CL.new_client_id(one, "North Star") == "north-star"
    assert CL.new_client_id(one, "One") == "one-2", "a taken slug takes the next free number"
    assert CL.new_client_id(one, "Ação") == "acao", "an accented name becomes ASCII"
    assert CL.new_client_id(one, "  ") == "client", "a name with no word still makes an id"
