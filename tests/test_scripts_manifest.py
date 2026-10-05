"""Guard: every tracked path under scripts/ has one manifest row with a reason (#113, ADR-0018).

A row names a file or a directory; a directory row covers the tracked files under it.
Untracked paths (scripts/archive/jev_data_real/) are not required.
"""
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "scripts" / "manifest.json"


def tracked():
    out = subprocess.run(["git", "ls-files", "scripts"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [p for p in out.splitlines() if p != "scripts/manifest.json"]


def check(rows, files, root=ROOT):
    errs = []
    paths = [r["path"] for r in rows]
    for p in {p for p in paths if paths.count(p) > 1}:
        errs.append(f"duplicate row: {p}")
    for r in rows:
        p = r["path"]
        if not (root / p).exists():
            errs.append(f"row for a missing path: {p}")
        if r.get("status") not in ("live", "archived"):
            errs.append(f"bad status: {p}")
        if not str(r.get("reason", "")).strip():
            errs.append(f"empty reason: {p}")
        if r.get("status") == "archived" and r.get("path", "").endswith(".py") or r.get("result"):
            if not r.get("result") or not (root / r["result"]).is_file() or not r["result"].startswith("docs/research/"):
                errs.append(f"one-off without an existing docs/research file: {p}")
    for f in files:
        n = sum(1 for p in paths if f == p or f.startswith(p + "/"))
        if n != 1:
            errs.append(f"{n} rows for {f}")
    return errs


def test_manifest_is_complete_and_true():
    assert check(json.loads(MANIFEST.read_text()), tracked()) == []


def test_missing_row_fails():
    rows = json.loads(MANIFEST.read_text())
    assert check(rows[1:], tracked())


def test_row_for_missing_path_fails():
    rows = json.loads(MANIFEST.read_text()) + [{"path": "scripts/nope.py", "status": "live", "reason": "x"}]
    assert any("missing path" in e for e in check(rows, tracked()))


def test_empty_reason_fails():
    rows = json.loads(MANIFEST.read_text())
    rows[0] = {**rows[0], "reason": " "}
    assert any("empty reason" in e for e in check(rows, tracked()))


def test_one_off_without_result_file_fails():
    rows = json.loads(MANIFEST.read_text())
    i = next(i for i, r in enumerate(rows) if r["path"].endswith("probe_limits.py"))
    rows[i] = {**rows[i], "result": "docs/research/none.md"}
    assert any("one-off" in e for e in check(rows, tracked()))


def test_finished_probes_live_under_archive_and_no_doc_names_an_old_path():
    """Guard for #114: the finished probes sit under scripts/archive/; no doc or script names their old paths."""
    import re
    old = ["probe_jev", "probe_limits", "probe_node_deadline", "probe_live_smoke", "probe_commands", "probe_slash",
           "jev_real", "jev_data", "jev_data_real", "jev_results_real"]
    for name in old:
        assert not (ROOT / "scripts" / name).exists() and not (ROOT / "scripts" / f"{name}.py").exists(), name
    pat = re.compile(r"scripts/(?!archive/)(" + "|".join(old) + r")\b")
    files = subprocess.run(["git", "ls-files", "docs", "scripts", "tests", ".gitignore"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    for f in files:
        p = ROOT / f
        if p.suffix in (".py", ".md", ".json", "") and p.is_file() and f != "tests/test_scripts_manifest.py":
            assert not pat.search(p.read_text(errors="ignore")), f
