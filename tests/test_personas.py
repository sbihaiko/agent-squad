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
