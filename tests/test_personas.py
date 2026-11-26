"""GitHub issue #3010: the rules of the Personas theme (SPEC 3009).

The Personas theme lists the personas of every roster project, grouped by client and merged by persona id inside a
client. It reads the same two files the Slack theme reads: the committed `slack.yaml` and the profile's
`slack.local.yaml`, and the local client map that groups the projects.

The seam under test is the persona rules module itself, over plain documents, like the client map tests: `groups` and
the seat it reads. No hub, no file and no page here.
"""
from squad import clients as CL
from squad import personas as PS


def seat(pid, committed=None, local=None, cf="slack.yaml", lf="slack.local.yaml"):
    """One roster project: its id and label, its two documents, and the file name each one states."""
    return {"id": pid, "label": pid, "committed": committed or {}, "local": local or {}, "cf": cf, "lf": lf}


def persona(pid, name, voice="Kore", tone="Calm."):
    return {"id": pid, "name": name, "voice": voice, "tone": tone}


def one_client(projects, persona_id=None, client_id="acme"):
    default = {"id": persona_id, "name": persona_id} if persona_id else None
    return CL.normalize({"clients": [{"id": client_id, "name": client_id.title(), "default_persona": default,
                                      "projects": projects}]})


def test_one_row_for_each_client_and_persona_id():
    """#3010 acceptance 3: two projects of one client that declare one id draw one row. A second client that declares
    the same id draws its own row, so a row names one client alone."""
    seats = [seat("alpha", {"personas": [persona("hal", "HAL9K")]}),
             seat("beta", {"personas": [persona("hal", "HAL9K")]})]
    clients = CL.normalize({"clients": [{"id": "acme", "name": "Acme", "projects": ["alpha", "beta"]},
                                        {"id": "globex", "name": "Globex", "projects": ["gamma"]}]})
    seats.append(seat("gamma", {"personas": [persona("hal", "HAL9K")]}))
    groups = PS.groups(seats, clients)
    assert [g["id"] for g in groups] == ["acme", "globex"], groups
    assert [[r["id"] for r in g["rows"]] for g in groups] == [["hal"], ["hal"]], groups


def test_a_row_names_the_declaring_projects_and_the_file_of_each_declaration():
    """#3010 acceptance 4: each declaration names its project, its label and the file the persona is written in. A
    persona the local file alone declares names the local file."""
    seats = [seat("alpha", {"personas": [persona("hal", "HAL9K")]}),
             seat("beta", {}, {"personas": [persona("hal", "HAL9K")]}, lf="beta.local.slack.yaml")]
    groups = PS.groups(seats, one_client(["alpha", "beta"]))
    row = groups[0]["rows"][0]
    assert row["projects"] == [{"id": "alpha", "label": "alpha", "file": "slack.yaml"},
                               {"id": "beta", "label": "beta", "file": "beta.local.slack.yaml"}], row


def test_a_row_names_each_client_whose_default_persona_is_that_id():
    """#3010 acceptance 5: the row carries every client that names the persona as its default, whichever project of the
    client declares it."""
    seats = [seat("alpha", {"personas": [persona("hal", "HAL9K"), persona("iara", "Iara")]})]
    clients = CL.normalize({"clients": [{"id": "acme", "name": "Acme", "default_persona": "hal", "projects": ["alpha"]},
                                        {"id": "globex", "name": "Globex", "default_persona": "iara",
                                         "projects": ["alpha"]}]})
    rows = {r["id"]: r for g in PS.groups(seats, clients) for r in g["rows"]}
    assert rows["hal"]["default_of"] == ["acme"], rows["hal"]
    assert rows["iara"]["default_of"] == ["globex"], rows["iara"]


def test_a_default_names_every_client_in_each_group_that_declares_the_id():
    """#3010 acceptance 5 names the clients whose default the id is, not the clients of one group, so two clients that
    each own a project declaring one id carry both names on each group's row."""
    seats = [seat("alpha", {"personas": [persona("hal", "HAL9K")]}),
             seat("beta", {"personas": [persona("hal", "HAL9K")]})]
    clients = CL.normalize({"clients": [{"id": "acme", "name": "Acme", "default_persona": "hal", "projects": ["alpha"]},
                                        {"id": "globex", "name": "Globex", "default_persona": "hal",
                                         "projects": ["beta"]}]})
    groups = PS.groups(seats, clients)
    assert [g["id"] for g in groups] == ["acme", "globex"], groups
    assert [g["rows"][0]["default_of"] for g in groups] == [["acme", "globex"], ["acme", "globex"]], groups


def test_two_projects_of_one_client_that_declare_different_fields_are_a_drift():
    """#3010 acceptance 6: the client's row carries the first declaration's fields and the drift flag, because the
    second project declares another name, voice or tone."""
    seats = [seat("alpha", {"personas": [persona("hal", "HAL9K", "Kore", "Calm.")]}),
             seat("beta", {"personas": [persona("hal", "HAL", "Kore", "Calm.")]})]
    row = PS.groups(seats, one_client(["alpha", "beta"]))[0]["rows"][0]
    assert row["drift"] is True, row
    assert (row["name"], row["voice"], row["tone"]) == ("HAL9K", "Kore", "Calm."), row


def test_a_drifted_row_names_each_copy_with_its_project():
    """#3015 acceptance 1: the drifted row names each differing copy with the project that declares it, so the owner
    reads every version and no version hides behind the first."""
    seats = [seat("alpha", {"personas": [persona("hal", "HAL9K", "Kore", "Calm.")]}),
             seat("beta", {"personas": [persona("hal", "HAL", "Sulafat", "Warm.")]})]
    row = PS.groups(seats, one_client(["alpha", "beta"]))[0]["rows"][0]
    assert row["copies"] == [
        {"project": "alpha", "label": "alpha", "name": "HAL9K", "voice": "Kore", "tone": "Calm.", "file": "slack.yaml"},
        {"project": "beta", "label": "beta", "name": "HAL", "voice": "Sulafat", "tone": "Warm.", "file": "slack.yaml"},
    ], row


def test_a_drift_names_a_local_only_copy_that_differs_from_the_committed_one():
    """A drift covers every declaration of one client, so a committed copy and a local-only copy of one id with
    different fields still raise the flag; the copies a save can write stay the committed ones alone."""
    seats = [seat("alpha", {"personas": [persona("hal", "HAL9K", "Kore", "Calm.")]}),
             seat("beta", {}, {"personas": [persona("hal", "HAL", "Sulafat", "Warm.")]})]
    row = PS.groups(seats, one_client(["alpha", "beta"]))[0]["rows"][0]
    assert row["drift"] is True, row
    assert [c["project"] for c in row["copies"]] == ["alpha"], row


def test_a_settle_of_a_row_with_no_drift_names_its_reason():
    """A settle exists for a drifted row alone: projects that declare the same fields never offer a choice, so a body
    that names a copy answers a reason and no target."""
    seats = [seat(pid, {"personas": [persona("hal", "HAL9K")]}) for pid in ("alpha", "beta", "gamma")]
    got = PS.settle(seats, one_client(["alpha", "beta", "gamma"]), "hal", "acme", "alpha")
    assert got["fields"] is None and got["targets"] == [] and got["err"], got


def test_a_settle_names_the_chosen_copy_and_only_that_client_s_projects():
    """#3015 acceptance 2, 3 and 6: the choice answers the chosen copy's fields and the projects of that client that
    declare the id; a project of another client that declares the same id is never a target."""
    seats = [seat("alpha", {"personas": [persona("hal", "HAL9K", "Kore", "Calm.")]}),
             seat("beta", {"personas": [persona("hal", "HAL", "Sulafat", "Warm.")]}),
             seat("gamma", {"personas": [persona("hal", "Gamma", "Puck", "Direct.")]}),
             seat("delta", {"personas": [persona("iara", "Iara")]})]
    clients = CL.normalize({"clients": [{"id": "acme", "name": "Acme", "projects": ["alpha", "beta", "delta"]},
                                        {"id": "globex", "name": "Globex", "projects": ["gamma"]}]})
    got = PS.settle(seats, clients, "hal", "acme", "beta")
    assert got["err"] is None, got
    assert got["fields"] == {"name": "HAL", "voice": "Sulafat", "tone": "Warm."}, got
    assert got["targets"] == ["alpha", "beta"], got


def test_a_settle_of_a_copy_of_another_client_names_its_reason():
    """#3015 acceptance 6: a project that belongs to another client answers no target for this client's row."""
    seats = [seat("alpha", {"personas": [persona("hal", "HAL9K")]}),
             seat("gamma", {"personas": [persona("hal", "Gamma")]})]
    clients = CL.normalize({"clients": [{"id": "acme", "name": "Acme", "projects": ["alpha"]},
                                        {"id": "globex", "name": "Globex", "projects": ["gamma"]}]})
    got = PS.settle(seats, clients, "hal", "acme", "gamma")
    assert got["targets"] == [] and got["fields"] is None and got["err"], got


def test_two_clients_that_declare_one_id_with_different_fields_are_no_drift():
    """#3010 acceptance 7: drift lives inside one client, so the same id declared with another tone by another client
    carries no drift flag on either row."""
    seats = [seat("alpha", {"personas": [persona("hal", "HAL9K", "Kore", "Calm.")]}),
             seat("gamma", {"personas": [persona("hal", "HAL9K", "Kore", "Warm.")]})]
    clients = CL.normalize({"clients": [{"id": "acme", "name": "Acme", "projects": ["alpha"]},
                                        {"id": "globex", "name": "Globex", "projects": ["gamma"]}]})
    rows = [r for g in PS.groups(seats, clients) for r in g["rows"]]
    assert [(r["id"], r["tone"], r["drift"]) for r in rows] == [("hal", "Calm.", False), ("hal", "Warm.", False)], rows


def test_the_committed_row_wins_over_a_local_row_with_the_same_id():
    """#3010 acceptance 8: when both files of one project declare the id, the committed fields answer the row and the
    declaration names the committed file."""
    seats = [seat("alpha", {"personas": [persona("hal", "HAL9K", "Kore", "Calm.")]},
                  {"personas": [persona("hal", "HAL", "Sulafat", "Warm.")]})]
    row = PS.groups(seats, one_client(["alpha"]))[0]["rows"][0]
    assert (row["name"], row["voice"], row["tone"]) == ("HAL9K", "Kore", "Calm."), row
    assert row["projects"] == [{"id": "alpha", "label": "alpha", "file": "slack.yaml"}], row


def test_a_persona_only_a_local_file_declares_reads_read_only_with_its_file():
    """#3010 acceptance 9: a persona no committed file declares is read-only, and the row names the local file that
    declares it."""
    seats = [seat("alpha", {}, {"personas": [persona("iara", "Iara")]}, lf="alpha.local.slack.yaml")]
    row = PS.groups(seats, one_client(["alpha"]))[0]["rows"][0]
    assert row["read_only"] is True, row
    assert [p["file"] for p in row["projects"]] == ["alpha.local.slack.yaml"], row


def test_a_project_in_no_client_forms_its_own_group():
    """#3010 acceptance 10: a project the map does not name draws its own group, under its own label, after the
    clients."""
    seats = [seat("alpha", {"personas": [persona("hal", "HAL9K")]}),
             seat("ghost", {"personas": [persona("iara", "Iara")]})]
    groups = PS.groups(seats, one_client(["alpha"]))
    assert [g["id"] for g in groups] == ["acme", "ghost"], groups
    assert groups[1]["name"] == "ghost", groups[1]
    assert [r["id"] for r in groups[1]["rows"]] == ["iara"], groups[1]


def test_the_seat_reads_a_document_that_is_not_a_mapping_as_no_persona():
    """A broken document never names a persona: the rules read a plain mapping and treat anything else as empty."""
    seats = [seat("alpha", None, ["not", "a", "mapping"])]
    assert PS.groups(seats, one_client(["alpha"]))[0]["rows"] == []


# ---------------------------------------------------------------- one persona removed from a project (#3014)

def test_the_removal_drops_only_the_row_of_that_id():
    """#3014 acceptance 8: the committed document loses the persona's row alone, and every other row keeps its place."""
    doc = {"version": 1, "personas": [persona("hal", "HAL9K"), persona("iara", "Iara")],
           "project": {"persona": "iara"}}
    got = PS.without(doc, "hal")
    assert got["personas"] == [persona("iara", "Iara")], got
    assert got["project"] == {"persona": "iara"}, got
    assert len(doc["personas"]) == 2, "the input document changed"


def test_a_persona_the_committed_file_does_not_declare_answers_no_document():
    """A persona only the local file declares is read-only: the rules answer no document for it."""
    assert PS.without({"personas": [persona("iara", "Iara")]}, "hal") is None


def test_a_removal_with_no_holder_writes_the_document_without_the_row():
    """#3014 acceptance 8: a plan of a project whose files name no such persona carries the committed document a
    confirmed save writes, and it holds no row of that id and keeps every other key."""
    seats = [{**seat("alpha", {"personas": [persona("hal", "HAL9K"), persona("iara", "Iara")],
                               "project": {"persona": "iara"}}),
              "checkout": "/co/alpha"}]
    plan = PS.remove(seats, "hal", ["alpha"], {})
    assert plan["rows"][0]["result"] == "removes HAL9K", plan["rows"]
    assert [w["committed"]["personas"] for w in plan["writes"]] == [[persona("iara", "Iara")]], plan["writes"]
    assert plan["writes"][0]["committed"]["project"] == {"persona": "iara"}, plan["writes"]


def test_a_refusal_names_only_a_persona_the_target_project_declares():
    """A project whose committed file does not declare the id is refused with the bare id, whatever another project's
    seat declares for that id: no other project's declaration names this project's persona."""
    seats = [{**seat("alpha", {"personas": [persona("hal", "HAL9K")]}), "checkout": "/co/alpha"},
             {**seat("beta", {"personas": [persona("iara", "Iara")]}), "checkout": "/co/beta"}]
    plan = PS.remove(seats, "hal", ["beta"], {})
    assert plan["rows"][0]["result"] == "refused: hal is not declared in this project's committed file", plan["rows"]
    assert plan["writes"] == [], plan["writes"]


def test_a_removal_with_a_holder_is_refused_and_names_each_holder():
    """#3014 acceptance 6: a project whose files name the persona is refused, and the refusal names every holder."""
    seats = [{**seat("alpha", {"personas": [persona("hal", "HAL9K")]}), "checkout": "/co/alpha"}]
    holders = {"alpha": ["project in slack.yaml", 'workspace "team" in slack.yaml']}
    plan = PS.remove(seats, "hal", ["alpha"], holders)
    assert plan["rows"][0]["result"] == ('refused: held by project in slack.yaml; workspace "team" in slack.yaml'), \
        plan["rows"]
    assert plan["writes"] == [], plan["writes"]


def test_the_removal_names_each_refused_project_and_still_carries_the_other_writes():
    """A target outside the roster, a project with no worktree, a file whose read answered a defect and a project whose
    committed file does not declare the id are each refused by name; the other targets keep their write."""
    seats = [{**seat("alpha", {"personas": [persona("hal", "HAL9K")]}), "checkout": "/co/alpha"},
             {**seat("beta", {"personas": [persona("hal", "HAL9K")]}), "checkout": ""},
             {**seat("gamma", {"personas": [persona("hal", "HAL9K")]}), "checkout": "/co/gamma",
                "read_defects": ["slack.yaml: not YAML"]},
             {**seat("delta", {"personas": [persona("iara", "Iara")]}), "checkout": "/co/delta"}]
    plan = PS.remove(seats, "hal", ["alpha", "beta", "gamma", "delta", "ghost"], {})
    rows = {r["project"]: r["result"] for r in plan["rows"]}
    assert rows["alpha"] == "removes HAL9K", rows
    assert rows["beta"] == "refused: the project has no worktree", rows
    assert rows["gamma"] == "refused: the file does not read: slack.yaml: not YAML", rows
    # The name a refusal states comes from the target project alone, so delta, which declares no name for `hal`, is
    # refused with the bare id rather than with alpha's name for it.
    assert rows["delta"] == "refused: hal is not declared in this project's committed file", rows
    assert rows["ghost"] == "refused: the project is not in the roster", rows
    assert [w["project"] for w in plan["writes"]] == ["alpha"], plan["writes"]
