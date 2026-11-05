"""A go-labelled issue with no readable card is named, and a node id known from an earlier read rescues it (#800)."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

SCRIPT = Path(__file__).parents[1] / "contrib" / "squad-goal" / "scripts" / "issue_state.py"
SPEC = importlib.util.spec_from_file_location("issue_state", SCRIPT)
issue_state = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(issue_state)


class FakeBinding:
    name = "fake"

    def __init__(self, carried, nodes=None, facts=None):
        self.carried = carried
        self.nodes = nodes or {}
        self.facts = facts
        self.asked = []
        self.reads = 0

    def read_all(self):
        self.reads += 1
        return dict(self.carried)

    def node_status(self, issue):
        self.asked.append(str(issue))
        return self.nodes.get(str(issue))

    def issue_facts(self, issue):
        """The issue's own (state, labels), or None when the fixture does not answer for it."""
        return (self.facts or {}).get(str(issue))


def test_unread_names_a_go_issue_the_board_read_does_not_carry():
    binding = FakeBinding({"801": "Todo"})

    carried, rescued, missing = issue_state.State(binding).unread([800, 801, 802])

    assert carried == {"801": "Todo"}
    assert rescued == {}
    assert missing == ["800", "802"]


def test_unread_rescues_a_missing_card_that_its_node_id_resolves_in_todo():
    binding = FakeBinding({"801": "Todo"}, nodes={"800": "Todo"})

    _, rescued, missing = issue_state.State(binding).unread([800, 801])

    assert rescued == {"800": "Todo"}
    assert missing == []


def test_unread_reads_no_node_for_a_card_the_board_read_carries():
    binding = FakeBinding({"801": "Todo"}, nodes={"801": "Code Review"})

    issue_state.State(binding).unread([801])

    assert binding.asked == []


def test_node_status_reads_the_status_of_the_item_its_known_id_names(tmp_path):
    calls = []
    answer = {"isArchived": False, "content": {"number": 800},
              "fields": [{"name": "Status", "value": {"name": "Todo"}}]}

    def run(args, **kw):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout=json.dumps(answer), stderr="")

    b = issue_state.BoardBinding("owner", 7, "owner/ariel", run=run, env={"SG_STATE": str(tmp_path)}, state_dir=str(tmp_path))
    (tmp_path / b._known_file).write_text(json.dumps({"800": "PVTI_x"}))

    assert b.node_status(800) == "Todo"
    assert any("PVTI_x" in " ".join(a) for a in calls)
    assert not any("graphql" in a for c in calls for a in c), "the loop reads GitHub over REST only (#1303)"


def test_the_one_card_read_asks_for_the_status_field_by_id(tmp_path):
    """#1305: the one-item route answers the title alone unless the Status field is asked for by id, exactly as the
    paginated read does. A read that does not ask names no status, so every write that landed reads as a read
    failure and its proof never comes -- the dispatch whose card write is pending then waits for good."""
    calls = []

    def run(args, **kw):
        calls.append(args)
        path = " ".join(args)
        if "/fields?per_page=" in path:
            return SimpleNamespace(returncode=0, stderr="", stdout=json.dumps(
                [{"name": "Status", "id": "F_STATUS", "options": [{"name": {"raw": "Todo"}, "id": "O1"}]}]))
        return SimpleNamespace(returncode=0, stderr="", stdout=json.dumps(
            {"isArchived": False, "content": {"number": 800},
             "fields": [{"name": "Status", "value": {"name": {"raw": "Todo", "html": "Todo"}}}]}))   # the route's own shape

    b = issue_state.BoardBinding("owner", 7, "owner/ariel", run=run, env={"SG_STATE": str(tmp_path)}, state_dir=str(tmp_path))
    (tmp_path / b._known_file).write_text(json.dumps({"800": "PVTI_x"}))

    assert b.node_status(800) == "Todo"
    item_calls = [a for a in calls if any("PVTI_x" in x for x in a)]
    assert item_calls, calls
    assert any(any("fields[]=F_STATUS" in x for x in a) for a in item_calls), \
        f"the one-card read asks for the Status field by id: {item_calls}"


def test_node_status_without_a_known_id_makes_no_call(tmp_path):
    calls = []

    def run(args, **kw):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="{}", stderr="")

    b = issue_state.BoardBinding("owner", 7, "owner/ariel", run=run, env={"SG_STATE": str(tmp_path)}, state_dir=str(tmp_path))

    assert b.node_status(800) is None
    assert calls == []


def test_a_node_id_seen_in_one_read_is_kept_for_the_next(tmp_path):
    b = issue_state.BoardBinding("owner", 7, "owner/ariel", run=None, env={"SG_STATE": str(tmp_path)}, state_dir=str(tmp_path))
    b.items_map = {"800": "PVTI_x", "801": "PVTI_y"}

    b._save_items()
    b.items_map = {"801": "PVTI_y"}   # a later read no longer carries 800
    b._save_items()

    assert json.loads((tmp_path / b._known_file).read_text()) == {"800": "PVTI_x", "801": "PVTI_y"}


def test_fresh_cache_go_read_rescues_missing_requested_issue_without_board_read(tmp_path, monkeypatch, capsys):
    cache = tmp_path / "hub-board.json"
    cache.write_text(json.dumps({"t": issue_state.time.time(), "repo": "owner/ariel",
                                 "items": [{"n": 801, "st": "Todo", "r": "owner/ariel"}]}))
    monkeypatch.setenv("SG_BOARD_CACHE", str(cache))
    monkeypatch.setenv("SG_REPO", "owner/ariel")
    binding = FakeBinding({"801": "Todo"}, nodes={"800": "Todo"})
    monkeypatch.setattr(issue_state, "board_state", lambda repo="": issue_state.State(binding))

    assert issue_state.main(["issue_state.py", "--go", "800,801"]) == 0

    assert capsys.readouterr().out.splitlines() == ["800\tTodo", "801\tTodo"]
    assert binding.reads == 0
    assert binding.asked == ["800"]


def test_fresh_cache_is_bypassed_for_another_repository(tmp_path, monkeypatch):
    cache = tmp_path / "hub-board.json"
    cache.write_text(json.dumps({"t": issue_state.time.time(), "repo": "owner/ariel",
                                 "items": [{"n": 801, "st": "Todo", "r": "owner/ariel"}]}))
    monkeypatch.setenv("SG_BOARD_CACHE", str(cache))
    monkeypatch.setenv("SG_REPO", "owner/ariel")
    monkeypatch.setattr(issue_state, "board_state", lambda repo="": issue_state.State(FakeBinding({"999": "Done"})))
    import io
    from contextlib import redirect_stdout
    out = io.StringIO()
    with redirect_stdout(out):
        assert issue_state.main(["issue_state.py", "--repo", "other/repo", "--go", "801"]) == 0
    assert out.getvalue().splitlines() == ["999\tDone", "UNREAD\t801"]


def test_missing_and_stale_cache_fall_back_to_board_read(tmp_path, monkeypatch, capsys):
    cache = tmp_path / "hub-board.json"
    monkeypatch.setenv("SG_BOARD_CACHE", str(cache))
    monkeypatch.setenv("SG_REPO", "owner/ariel")
    calls = []

    def read(repo=None):
        calls.append(repo)
        return issue_state.State(FakeBinding({"801": "Todo"}))

    monkeypatch.setattr(issue_state, "board_state", read)
    for payload in (None, {"t": issue_state.time.time() - 301, "repo": "owner/ariel", "items": []}):
        if payload is None:
            cache.unlink(missing_ok=True)
        else:
            cache.write_text(json.dumps(payload))
        assert issue_state.main(["issue_state.py", "--go", "801"]) == 0
        assert capsys.readouterr().out.splitlines() == ["801\tTodo"]
    assert calls == ["owner/ariel", "owner/ariel"], "the door names SG_REPO when no --repo does (#1429)"


def test_go_check_prints_the_unread_line_and_alerts(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SG_STATE", str(tmp_path))
    monkeypatch.setattr(issue_state, "board_state", lambda repo="": issue_state.State(FakeBinding({"801": "Todo"})))

    assert issue_state.main(["issue_state.py", "--go", "800,801"]) == 0

    out = capsys.readouterr().out.splitlines()
    assert "801\tTodo" in out
    assert "UNREAD\t800" in out
    assert "800" in (tmp_path / "alerts.log").read_text()


# #1031: the go list was read at another time, and the rows come from a cache the owner and the project share. An issue
# the list names is named go-labelled only when the issue itself agrees; one that answers closed, or without the label,
# is said once and never alerts, and a card the board really cannot read still alerts.


def test_a_closed_issue_named_by_the_list_is_never_named_go_labelled(capsys):
    binding = FakeBinding({"801": "Todo"}, facts={"800": ("closed", ["bug", "go"])})

    _, rescued, missing = issue_state.State(binding).unread([800, 801], "go")

    assert rescued == {} and missing == []
    out = capsys.readouterr().out
    assert "NOTE\t800\tclosed" in out


def test_an_open_issue_the_issue_itself_says_has_no_go_label_is_never_named_go_labelled(capsys):
    binding = FakeBinding({"801": "Todo"}, facts={"800": ("open", ["bug"])})

    _, _, missing = issue_state.State(binding).unread([800, 801], "go")

    assert missing == []
    assert "NOTE\t800\tno go label" in capsys.readouterr().out


def test_a_card_the_board_really_cannot_read_still_alerts():
    binding = FakeBinding({"801": "Todo"}, facts={"800": ("open", ["bug", "go"])})

    _, _, missing = issue_state.State(binding).unread([800, 801], "go")

    assert missing == ["800"]


def test_a_read_that_does_not_answer_still_alerts():
    """A premise a failed read never settles one way or the other (ADR-0155): the issue stays missing."""
    binding = FakeBinding({"801": "Todo"}, facts={"800": None})

    _, _, missing = issue_state.State(binding).unread([800, 801], "go")

    assert missing == ["800"]


def test_a_closed_issue_the_stale_rows_still_carry_drops_out_of_the_next_cycle():
    """Closing an issue drops it from the go set on the next cycle (AC2), even when the rows are an earlier read
    that still carries its Todo card: the rows are a cache, and the issue is the one source that settles it (#1031)."""
    opened = issue_state.State(FakeBinding({"801": "Todo"}, facts={"801": ("open", ["bug", "go"])}))
    carried, _, missing = opened.unread_rows({"801": "Todo"}, ["801"], "go")
    assert carried == {"801": "Todo"} and missing == [], "an open go-labelled issue the rows carry is in the go set"

    closed = issue_state.State(FakeBinding({"801": "Todo"}, facts={"801": ("closed", ["bug", "go"])}))
    carried, _, missing = closed.unread_rows({"801": "Todo"}, ["801"], "go")
    assert carried == {}, "a closed issue the stale rows still carry is not in the go set"
    assert missing == []


def test_a_closed_issue_the_node_would_answer_is_not_rescued_on_the_next_cycle():
    """The node path is not a way back in: an issue that closed between two cycles is dropped before its node is
    asked, so a closed card is never rescued into the go set (#1031, AC2)."""
    opened = issue_state.State(FakeBinding({}, nodes={"800": "Todo"}, facts={"800": ("open", ["bug", "go"])}))
    _, rescued, missing = opened.unread_rows({}, ["800"], "go")
    assert rescued == {"800": "Todo"} and missing == []

    binding = FakeBinding({}, nodes={"800": "Todo"}, facts={"800": ("closed", ["bug", "go"])})
    _, rescued, missing = issue_state.State(binding).unread_rows({}, ["800"], "go")
    assert rescued == {}, "a closed issue is never rescued into the go set"
    assert binding.asked == [], "a closed issue's node is never asked"
    assert missing == []


def _binding(tmp_path, answer):
    def run(args, **kw):
        return SimpleNamespace(returncode=answer[0], stdout=answer[1], stderr=answer[2])
    return issue_state.BoardBinding("owner", 7, "owner/ariel", run=run, env={"SG_STATE": str(tmp_path)}, state_dir=str(tmp_path))


def test_issue_facts_reads_the_state_and_labels_of_the_issue_itself(tmp_path):
    body = json.dumps({"number": 801, "state": "CLOSED", "labels": [{"name": "bug"}]})
    assert _binding(tmp_path, (0, body, "")).issue_facts(801) == ("closed", ["bug"])


def test_issue_facts_is_none_when_the_read_does_not_answer(tmp_path):
    refused = _binding(tmp_path / "a", (1, "", "HTTP 403: API rate limit exceeded"))
    hung = _binding(tmp_path / "b", (0, "", ""))
    assert refused.issue_facts(801) is None, "a refused read settles nothing (ADR-0155)"
    assert hung.issue_facts(801) is None


def test_issue_facts_is_none_for_a_label_list_it_cannot_read(tmp_path):
    """An answer whose labels are not label objects settles nothing (ADR-0155): the naming rule must not read an
    answer it cannot interpret as the issue denying its go label (#1364)."""
    body = json.dumps({"number": 801, "state": "open", "labels": ["bug", "go"]})
    assert _binding(tmp_path, (0, body, "")).issue_facts(801) is None
    bare = json.dumps({"number": 801, "state": "open"})
    assert _binding(tmp_path / "b", (0, bare, "")).issue_facts(801) is None, "no label list at all is unreadable too"


def test_issue_facts_is_none_for_an_answer_about_another_issue(tmp_path):
    body = json.dumps({"number": 999, "state": "open", "labels": [{"name": "go"}]})
    assert _binding(tmp_path, (0, body, "")).issue_facts(801) is None


def test_the_fresh_cache_path_applies_the_same_naming_rule(tmp_path, monkeypatch, capsys):
    cache = tmp_path / "hub-board.json"
    cache.write_text(json.dumps({"t": issue_state.time.time(), "repo": "owner/ariel",
                                 "items": [{"n": 801, "st": "Todo", "r": "owner/ariel"}]}))
    monkeypatch.setenv("SG_BOARD_CACHE", str(cache))
    monkeypatch.setenv("SG_REPO", "owner/ariel")   # the cache names this repository, so the cache path answers it
    monkeypatch.setenv("SG_STATE", str(tmp_path))
    binding = FakeBinding({"801": "Todo"}, facts={"800": ("closed", ["bug", "go"]), "802": ("open", ["bug"])})
    monkeypatch.setattr(issue_state, "board_state", lambda repo="": issue_state.State(binding))

    assert issue_state.main(["issue_state.py", "--go", "800,801,802", "--label", "go"]) == 0

    out = capsys.readouterr().out.splitlines()
    assert "801\tTodo" in out
    assert not any(line.startswith("UNREAD") for line in out), f"nothing the issue itself denies is named unread: {out}"
    assert not (tmp_path / "alerts.log").exists(), "an issue with no go label and a closed one never alert"
    assert binding.reads == 0, "the fresh cache answers the rows: the naming rule ran on the cache path, not a board read"


# #1102: a closed issue is never alerted, and an issue whose own read did not answer is named as that failed read, once.


def _go_pass(monkeypatch, tmp_path, binding, go, label="go"):
    monkeypatch.setenv("SG_STATE", str(tmp_path))
    monkeypatch.delenv("SG_BOARD_CACHE", raising=False)
    monkeypatch.setattr(issue_state, "board_state", lambda repo="": issue_state.State(binding))
    assert issue_state.main(["issue_state.py", "--go", go, "--label", label]) == 0


def test_a_go_issue_whose_issue_read_did_not_answer_is_named_as_that_failed_read(tmp_path, monkeypatch):
    binding = FakeBinding({"801": "Todo"}, facts={"800": None})

    _go_pass(monkeypatch, tmp_path, binding, "800,801")

    alert = (tmp_path / "alerts.log").read_text()
    assert "#800" in alert and "issue read did not answer" in alert, alert


def test_an_issue_already_named_unread_is_not_named_again_on_the_next_pass(tmp_path, monkeypatch):
    binding = FakeBinding({"801": "Todo"}, facts={"800": None})

    for _ in range(3):
        _go_pass(monkeypatch, tmp_path, binding, "800,801")

    assert len((tmp_path / "alerts.log").read_text().splitlines()) == 1


def test_a_closed_issue_whose_card_is_gone_is_never_alerted_on_any_pass(tmp_path, monkeypatch, capsys):
    binding = FakeBinding({"801": "Todo"}, facts={"800": ("closed", ["bug", "go"])})

    for _ in range(3):
        _go_pass(monkeypatch, tmp_path, binding, "800,801")

    assert not (tmp_path / "alerts.log").exists()
    assert "NOTE\t800\tclosed" in capsys.readouterr().out


def test_an_add_with_no_repository_refuses_and_names_its_cause(tmp_path):
    """#1315: `item_id(n, add_missing=True)` ran the add only when a repository was set, so with none it wrote nothing and
    board.py printed "not on the board" -- a fact that was false, for a write that never ran. The add refuses instead, and
    the refusal names the missing SG_REPO."""
    def run(args, **kw):
        return SimpleNamespace(returncode=0, stderr="", stdout="")   # the items read, one --jq line per card: no card at all

    b = issue_state.BoardBinding("owner", 7, "", run=run, env={"SG_STATE": str(tmp_path)}, state_dir=str(tmp_path))
    try:
        b.item_id(800, add_missing=True)
    except RuntimeError as e:
        assert "SG_REPO" in str(e), str(e)
    else:
        raise AssertionError("an add with no repository wrote nothing and refused nothing")
