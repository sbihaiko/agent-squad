"""#749: a review model that times out in a row is omitted from the next review chains for a window, by time.

The helper is a script beside review.sh. Each case drives it with an injected clock (`--now`), so the window is exact
and no case waits. The state file is the one the loop keeps in its state directory.
"""
import json
import pathlib
import subprocess
import sys

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "contrib" / "squad-goal" / "scripts" / "review-timeouts.py"
MODEL = "claude:slow"
BOUND, WINDOW = "3", "21600"
T0 = 1_000_000


def call(state, command, now, bound=BOUND, window=WINDOW):
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(state), command, MODEL, "--bound", bound, "--window", window, "--now", str(now)],
        capture_output=True, text=True, check=False,
    )


def timeouts(state, n, start=T0):
    for i in range(n):
        assert call(state, "timeout", start + i).returncode == 0


def test_three_timeouts_in_a_row_omit_the_model(tmp_path):
    state = tmp_path / "review-timeouts.json"
    timeouts(state, 3)
    res = call(state, "omitted", T0 + 10)
    assert res.returncode == 0
    assert "3 consecutive timeouts" in res.stdout
    assert "\n" not in res.stdout.strip()


def test_the_bound_line_names_the_omission_once(tmp_path):
    state = tmp_path / "review-timeouts.json"
    timeouts(state, 2)
    assert call(state, "timeout", T0 + 2).stdout.count("omitted until") == 1
    assert call(state, "timeout", T0 + 3).stdout.count("omitted until") == 1


def test_one_timeout_omits_nothing(tmp_path):
    state = tmp_path / "review-timeouts.json"
    timeouts(state, 1)
    assert call(state, "omitted", T0 + 10).returncode == 1


def test_two_timeouts_omit_nothing_at_the_default_bound(tmp_path):
    state = tmp_path / "review-timeouts.json"
    timeouts(state, 2)
    assert call(state, "omitted", T0 + 10).returncode == 1


def test_the_bound_is_read_from_the_argument(tmp_path):
    state = tmp_path / "review-timeouts.json"
    assert call(state, "timeout", T0, bound="2").stdout == ""
    assert call(state, "timeout", T0 + 1, bound="2").stdout.count("omitted until") == 1
    assert call(state, "omitted", T0 + 10).returncode == 0


def test_the_window_ends_by_time_and_restores_eligibility(tmp_path):
    state = tmp_path / "review-timeouts.json"
    timeouts(state, 3)
    end = T0 + 2 + int(WINDOW)
    assert call(state, "omitted", end - 1).returncode == 0
    assert call(state, "omitted", end).returncode == 1


def test_an_expired_omission_starts_a_new_streak(tmp_path):
    state = tmp_path / "review-timeouts.json"
    timeouts(state, 3)
    end = T0 + 2 + int(WINDOW)
    assert call(state, "omitted", end).returncode == 1
    assert call(state, "timeout", end + 1).stdout == ""
    assert call(state, "omitted", end + 2).returncode == 1


def test_an_answered_review_clears_the_streak(tmp_path):
    state = tmp_path / "review-timeouts.json"
    timeouts(state, 2)
    assert call(state, "clear", T0 + 2).returncode == 0
    timeouts(state, 1, start=T0 + 3)
    assert call(state, "omitted", T0 + 10).returncode == 1


def test_clear_restores_an_omitted_model(tmp_path):
    state = tmp_path / "review-timeouts.json"
    timeouts(state, 3)
    assert call(state, "clear", T0 + 5).returncode == 0
    assert call(state, "omitted", T0 + 10).returncode == 1


def test_state_is_one_json_file_with_no_leftovers(tmp_path):
    state = tmp_path / "review-timeouts.json"
    timeouts(state, 3)
    assert json.loads(state.read_text())[MODEL]["streak"] == 3
    assert sorted(p.name for p in tmp_path.iterdir()) == ["review-timeouts.json"]
