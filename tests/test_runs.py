"""Run folder resolution for `squad dashboard`. Resolves a reference to one run folder; it never starts a run."""
import json
import os
from pathlib import Path

import pytest

from squad import runs


def _run(root, name, ts, nested=False, run_id=None):
    d = root / name
    rec = (d / "run") if nested else d
    rec.mkdir(parents=True)
    (rec / "record.jsonl").write_text(json.dumps({"event": "run_start", "run_id": run_id or f"run-{name}", "ts": ts, "pos": 1}) + "\n")
    return d


def test_runs_are_numbered_oldest_first_from_one(tmp_path):
    _run(tmp_path, "b", 200.0)
    _run(tmp_path, "a", 100.0)
    _run(tmp_path, "c", 300.0, nested=True)
    assert [r.folder.name for r in runs.list_runs(tmp_path)] == ["a", "b", "c"]
    assert [r.number for r in runs.list_runs(tmp_path)] == [1, 2, 3]


def test_a_folder_with_no_record_is_not_a_run(tmp_path):
    _run(tmp_path, "a", 100.0)
    (tmp_path / "empty").mkdir()
    (tmp_path / "loose.txt").write_text("x")
    assert [r.folder.name for r in runs.list_runs(tmp_path)] == ["a"]


def test_a_record_without_a_start_time_sorts_by_file_time(tmp_path):
    a = _run(tmp_path, "a", 100.0)
    (a / "record.jsonl").write_text("{}\n")
    os.utime(a / "record.jsonl", (500.0, 500.0))
    _run(tmp_path, "b", 200.0)
    assert [r.folder.name for r in runs.list_runs(tmp_path)] == ["b", "a"]


def test_no_reference_picks_the_latest_run(tmp_path):
    _run(tmp_path, "a", 100.0)
    _run(tmp_path, "b", 200.0)
    assert runs.resolve(tmp_path, None).folder.name == "b"


def test_a_number_picks_that_position(tmp_path):
    _run(tmp_path, "a", 100.0)
    _run(tmp_path, "b", 200.0)
    assert runs.resolve(tmp_path, "1").folder.name == "a"
    assert runs.resolve(tmp_path, 2).folder.name == "b"


def test_a_folder_name_or_a_run_id_picks_the_run(tmp_path):
    _run(tmp_path, "a", 100.0, run_id="run-20260930-000000-aaaaaa")
    _run(tmp_path, "b", 200.0)
    assert runs.resolve(tmp_path, "a").folder.name == "a"
    assert runs.resolve(tmp_path, "run-20260930-000000-aaaaaa").folder.name == "a"


@pytest.mark.parametrize("ref", ["0", "3", "-1", "nope"])
def test_an_unknown_reference_is_a_clear_error(tmp_path, ref):
    _run(tmp_path, "a", 100.0)
    _run(tmp_path, "b", 200.0)
    with pytest.raises(runs.RunNotFound) as e:
        runs.resolve(tmp_path, ref)
    assert "2 runs" in str(e.value) and "1-2" in str(e.value)


def test_no_runs_at_all_is_a_clear_error(tmp_path):
    with pytest.raises(runs.RunNotFound) as e:
        runs.resolve(tmp_path, None)
    assert "no runs" in str(e.value)
    with pytest.raises(runs.RunNotFound):
        runs.resolve(tmp_path / "missing", None)


def test_the_page_url_carries_the_run_folder_and_points_at_the_built_page(tmp_path):
    d = _run(tmp_path, "my run", 100.0)
    url = runs.page_url(runs.resolve(tmp_path, None))
    assert url.startswith("file://") and "agent-squad-run-dashboard.html#run=" in url
    assert "my%20run" in url and str(d.resolve()).replace(" ", "%20") in url
    assert Path(url.split("#")[0][len("file://"):]).exists()
