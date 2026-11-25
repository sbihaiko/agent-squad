"""GitHub issue #2827: the rules of the local client map (SPEC 2682).

The client map groups the projects of one customer across profiles, and the tree draws it as Client > Project. Two rules
of the map hold the tree together: the synthetic Unassigned node has an id no map client may take, and a project counts
as a project with Slack only when it declares a workspace or a persona.

The seam under test is the client map module itself: `normalize`, `groups` and `has_slack`.
"""
from squad import clients as CL


def test_the_synthetic_unassigned_id_is_reserved_for_the_unassigned_group():
    """A map client that takes the synthetic id is dropped, so the tree never draws two nodes with one id, and the
    projects it named fall to the one Unassigned group."""
    doc = {"clients": [{"id": CL.UNASSIGNED_ID, "name": "A real client", "projects": ["alpha"]},
                       {"id": "one", "name": "One", "projects": ["beta"]}]}
    groups = CL.groups(CL.normalize(doc), ["alpha", "beta"])
    ids = [g["id"] for g in groups]
    assert ids == ["one", CL.UNASSIGNED_ID], groups
    assert len(ids) == len(set(ids)), ids
    assert [p["id"] for p in groups[1]["projects"]] == ["alpha"], groups[1]


def test_a_project_counts_as_with_slack_only_with_a_workspace_or_a_persona():
    """A file that names only a version, a project block or a role is the project with no Slack; a workspace or a
    persona makes it a project with Slack, in either file."""
    assert CL.has_slack({"version": 1, "project": {"persona": "blue"}, "roles": [{"id": "m", "actions": ["status"]}]},
                        {}) is False
    assert CL.has_slack({"version": 1, "workspaces": [], "personas": []}, {}) is False
    assert CL.has_slack({"version": 1, "workspaces": [{"id": "T0TEAM0001"}]}, {}) is True
    assert CL.has_slack({}, {"version": 1, "personas": [{"id": "blue"}]}) is True
