"""The report over the labeled gate-load set (#1757): it reads the input file only and chooses no bound."""
import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "contrib" / "squad-goal" / "scripts"
HEADER = "finish\tsha\tkind\tscope\toutcome\tload_start\tload_finish\trerun"


def _report_module():
    spec = importlib.util.spec_from_file_location("gate_load_report", SCRIPTS / "gate_load_report.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _row(outcome, rerun, load_start, load_finish="0.5"):
    return f"1700000000\tabc123\tfast\tunit\t{outcome}\t{load_start}\t{load_finish}\t{rerun}"


def _input(tmp_path: Path, rows, source="python3 gate_load_set.py /state", written="2026-11-16T10:00:00Z") -> Path:
    path = tmp_path / "gate-load-set.tsv"
    lines = [f"# source-command: {source}", f"# written: {written}", HEADER, *rows]
    path.write_text("\n".join(lines) + "\n")
    return path


def test_missing_input_file_names_no_load_and_makes_no_bound(tmp_path):
    mod = _report_module()
    result = mod.report(tmp_path / "gate-load-set.tsv")
    assert result["set_size"] == 0
    assert result["sufficient"] is False
    assert result["passed_loads"] == []
    assert "input" in result["reason"]


def test_header_names_the_source_command_and_write_time(tmp_path):
    mod = _report_module()
    path = _input(tmp_path, [], source="python3 gate_load_set.py /x", written="2026-11-16T10:00:00Z")
    result = mod.report(path)
    assert result["source"] == "python3 gate_load_set.py /x"
    assert result["written"] == "2026-11-16T10:00:00Z"


def test_counts_red_passed_on_rerun_and_red_stayed_red(tmp_path):
    mod = _report_module()
    path = _input(tmp_path, [
        _row("fail", "pass", "3.10"),
        _row("fail", "pass", "2.40"),
        _row("fail", "fail", "2.90"),
        _row("pass", "pass", "1.00"),
        _row("fail", "-", "4.00"),
    ])
    result = mod.report(path)
    assert result["red_passed_on_rerun"] == 2
    assert result["red_stayed_red"] == 1
    assert result["set_size"] == 3


def test_names_the_loads_over_which_a_red_passed_on_rerun(tmp_path):
    mod = _report_module()
    path = _input(tmp_path, [
        _row("fail", "pass", "3.10"),
        _row("fail", "pass", "2.40"),
        _row("fail", "fail", "9.00"),
    ])
    result = mod.report(path)
    assert result["passed_loads"] == ["2.40", "3.10"]


def test_reads_the_input_file_and_not_the_loop_state(tmp_path, monkeypatch):
    mod = _report_module()
    state = tmp_path / "state"
    state.mkdir()
    (state / "gate-history").write_text("not a row\n")
    monkeypatch.setenv("SG_STATE", str(state))
    path = _input(tmp_path, [_row("fail", "pass", "3.10")])
    result = mod.report(path)
    assert result["red_passed_on_rerun"] == 1


def test_sufficient_needs_both_kinds_at_the_minimum(tmp_path):
    mod = _report_module()
    rows = [_row("fail", "pass", "3.00")] * mod.MIN_ROWS + [_row("fail", "fail", "2.00")] * (mod.MIN_ROWS - 1)
    assert mod.report(_input(tmp_path, rows))["sufficient"] is False
    rows.append(_row("fail", "fail", "2.00"))
    assert mod.report(_input(tmp_path, rows))["sufficient"] is True
