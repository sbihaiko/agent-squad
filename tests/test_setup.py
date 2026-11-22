"""`squad setup` (#191): plan by default, add with --apply, never overwrite."""
import subprocess

from squad import cli


def _repo(tmp_path):
    d = tmp_path / "proj"
    d.mkdir()
    subprocess.run(["git", "init", "-q", str(d)], check=True)
    return d


def _tree(d):
    return {str(p.relative_to(d)): p.read_bytes() for p in d.rglob("*") if p.is_file() and ".git" not in p.parts}


def test_apply_creates_adr_0000_and_the_index_with_the_project_name(tmp_path):
    d = _repo(tmp_path)
    assert cli.main(["setup", "--target", str(d), "--apply"]) == 0
    adr = (d / "docs/adr/0000-adrs-are-guides.md").read_text()
    assert adr.startswith("# ADR-0000") and "proj" in adr
    assert "0000-adrs-are-guides.md" in (d / "docs/adr/README.md").read_text()


def test_a_second_apply_changes_nothing(tmp_path):
    d = _repo(tmp_path)
    cli.main(["setup", "--target", str(d), "--apply"])
    before = _tree(d)
    assert cli.main(["setup", "--target", str(d), "--apply"]) == 0
    assert _tree(d) == before


def test_without_apply_nothing_is_written_and_the_plan_is_printed(tmp_path, capsys):
    d = _repo(tmp_path)
    assert cli.main(["setup", "--target", str(d)]) == 0
    assert _tree(d) == {}
    assert "would create: docs/adr/0000-adrs-are-guides.md" in capsys.readouterr().out


def test_an_existing_adr_file_is_never_overwritten_and_numbers_are_kept(tmp_path):
    d = _repo(tmp_path)
    (d / "docs/adr").mkdir(parents=True)
    (d / "docs/adr/0000-mine.md").write_text("# ADR-0000: Mine\n")
    (d / "docs/adr/0007-other.md").write_text("# ADR-0007: Other\n")
    cli.main(["setup", "--target", str(d), "--apply"])
    assert (d / "docs/adr/0000-mine.md").read_text() == "# ADR-0000: Mine\n"
    assert not (d / "docs/adr/0000-adrs-are-guides.md").exists()
    idx = (d / "docs/adr/README.md").read_text()
    assert "0007-other.md" in idx and "Other" in idx
    (d / "docs/adr/README.md").write_text("keep me\n")
    cli.main(["setup", "--target", str(d), "--apply"])
    assert (d / "docs/adr/README.md").read_text() == "keep me\n"


def _names(d):
    return sorted(p.name for p in (d / "docs/adr").glob("0*.md"))


def test_default_set_is_written_listed_and_has_four_sections(tmp_path):
    d = _repo(tmp_path)
    cli.main(["setup", "--target", str(d), "--apply", "--test-command", "make check", "--tracker", "GitHub Issues"])
    names = _names(d)
    assert any(n.startswith("0000-") for n in names) and len(names) == 5   # no language rule: not chosen
    idx = (d / "docs/adr/README.md").read_text()
    for n in names:
        text = (d / "docs/adr" / n).read_text()
        assert idx.count(n) == 1
        for sec in ("## Status", "## Context", "## Decision", "## Guard"):
            assert sec in text
        assert "None yet. This rule is a draft until a named guard test exists." in text
        assert "{" not in text
    assert "make check" in (d / "docs/adr" / [n for n in names if "red-test" in n][0]).read_text()
    assert "GitHub Issues" in (d / "docs/adr" / [n for n in names if "where-decisions" in n][0]).read_text()


def test_commit_rule_skipped_when_declined_and_language_rule_only_when_chosen(tmp_path):
    d = _repo(tmp_path)
    cli.main(["setup", "--target", str(d), "--apply", "--no-auto-commit"])
    assert not any("commits" in n for n in _names(d)) and not any("language" in n for n in _names(d))
    (tmp_path / "x").mkdir()
    e = _repo(tmp_path / "x")
    cli.main(["setup", "--target", str(e), "--apply", "--language", "US English"])
    assert any("commits" in n for n in _names(e))
    lang = [n for n in _names(e) if "language" in n]
    assert lang and "US English" in (e / "docs/adr" / lang[0]).read_text()


def test_new_adrs_follow_existing_numbers_and_rerun_adds_nothing(tmp_path):
    d = _repo(tmp_path)
    (d / "docs/adr").mkdir(parents=True)
    (d / "docs/adr/0007-other.md").write_text("# ADR-0007: Other\n")
    cli.main(["setup", "--target", str(d), "--apply"])
    assert "0008-a-failing-gate-is-a-defect.md" in _names(d)
    before = _tree(d)
    cli.main(["setup", "--target", str(d), "--apply"])
    assert _tree(d) == before


def test_empty_repo_gets_claude_md_with_a_bullet_per_adr_and_the_symlink(tmp_path):
    d = _repo(tmp_path)
    cli.main(["setup", "--target", str(d), "--apply"])
    text = (d / "CLAUDE.md").read_text()
    for h in ("Purpose", "Layout", "Rules", "Where decisions go", "Agent skills"):
        assert f"## {h}" in text
    for adr in sorted((d / "docs/adr").glob("0*.md")):
        line = [x for x in text.splitlines() if f"(docs/adr/{adr.name})" in x]
        assert len(line) == 1 and "Guard:" in line[0], adr.name
    assert (d / "AGENTS.md").is_symlink() and (d / "AGENTS.md").readlink().as_posix() == "CLAUDE.md"


def test_existing_claude_md_keeps_its_text_and_only_a_missing_section_is_appended(tmp_path):
    d = _repo(tmp_path)
    mine = "# Mine\n\n## Purpose\n\nMy text.\n\n## Rules\n\n- my own rule\n"
    (d / "CLAUDE.md").write_text(mine)
    cli.main(["setup", "--target", str(d), "--apply"])
    got = (d / "CLAUDE.md").read_text()
    assert got.startswith(mine) and "## Layout" in got and "## Agent skills" in got
    assert got.count("## Purpose") == 1 and got.count("## Rules") == 1 and "(docs/adr/" not in got
    again = _tree(d)
    cli.main(["setup", "--target", str(d), "--apply"])
    assert _tree(d) == again


def test_a_different_agents_md_is_reported_and_not_replaced(tmp_path, capsys):
    d = _repo(tmp_path)
    (d / "AGENTS.md").write_text("other text\n")
    cli.main(["setup", "--target", str(d), "--apply"])
    assert (d / "AGENTS.md").read_text() == "other text\n" and not (d / "AGENTS.md").is_symlink()
    assert "report: AGENTS.md" in capsys.readouterr().out


def test_without_symlinks_a_one_line_pointer_is_written_and_reported(tmp_path, monkeypatch, capsys):
    d = _repo(tmp_path)
    def no_link(*a, **k):
        raise OSError("no symlinks")
    monkeypatch.setattr("os.symlink", no_link)
    cli.main(["setup", "--target", str(d), "--apply"])
    assert (d / "AGENTS.md").read_text() == "See CLAUDE.md.\n"
    assert "cannot link AGENTS.md" in capsys.readouterr().out


def test_check_fails_for_a_rule_adr_with_no_bullet_and_passes_after_it_is_added(tmp_path, capsys):
    d = _repo(tmp_path)
    cli.main(["setup", "--target", str(d), "--apply"])
    assert cli.main(["setup", "--target", str(d), "--check"]) == 0
    text = (d / "CLAUDE.md").read_text()
    kept = "".join(ln for ln in text.splitlines(keepends=True) if "ADR-0000" not in ln)
    (d / "CLAUDE.md").write_text(kept)
    assert cli.main(["setup", "--target", str(d), "--check"]) == 1
    assert "ADR-0000" in capsys.readouterr().out
    (d / "CLAUDE.md").write_text(text)
    assert cli.main(["setup", "--target", str(d), "--check"]) == 0


def test_check_fails_for_a_bullet_with_a_dead_link_or_a_missing_guard_file(tmp_path, capsys):
    d = _repo(tmp_path)
    cli.main(["setup", "--target", str(d), "--apply"])
    text = (d / "CLAUDE.md").read_text()
    (d / "CLAUDE.md").write_text(text.replace("0000-adrs-are-guides.md", "0000-gone.md"))
    assert cli.main(["setup", "--target", str(d), "--check"]) == 1
    assert "does not resolve" in capsys.readouterr().out
    (d / "CLAUDE.md").write_text(text.replace("Guard: None yet. This rule is a draft until a named guard test exists.", "Guard: `tests/test_nope.py`", 1))
    assert cli.main(["setup", "--target", str(d), "--check"]) == 1
    assert "tests/test_nope.py" in capsys.readouterr().out


def _claude(d, extra=""):
    cli.main(["setup", "--target", str(d), "--apply"])
    p = d / "CLAUDE.md"
    p.write_text(p.read_text() + extra)
    return cli.main(["setup", "--target", str(d), "--check"])


def _fresh(tmp_path, name):
    (tmp_path / name).mkdir()
    return _repo(tmp_path / name)


def test_a_rules_and_pointers_file_passes_the_ledger_check(tmp_path):
    assert _claude(_repo(tmp_path), "\n- Guard `tests/x.py` covers #12 and #13.\n") == 0


def test_each_kind_of_ledger_line_is_flagged(tmp_path, capsys):
    cases = {
        "x" * 400 + "\n": "over 300",
        "- The audit ran on 2026-09-30 and found two defects.\n": "dated",
        "- The suite currently takes ten minutes.\n": "currently",
        "- As of now the parser is rewritten.\n": "as of",
        "- Ticket #12 is done.\n": "ticket status",
        "## Big\n\n" + "- rule\n" * 45: "lines, over 40",
    }
    for i, (text, word) in enumerate(cases.items()):
        d = _fresh(tmp_path, f"c{i}")
        assert _claude(d, "\n" + text) == 1, word
        assert word in capsys.readouterr().out


def test_the_limits_are_config_values_a_project_can_change(tmp_path, capsys):
    d = _repo(tmp_path)
    assert _claude(d, "\n" + "y" * 400 + "\n") == 1
    capsys.readouterr()
    (d / "docs" / "squad").mkdir(parents=True)
    (d / "docs" / "squad" / "defaults.yaml").write_text("claude_md:\n  max_line_width: 500\n")
    assert cli.main(["setup", "--target", str(d), "--check"]) == 0


def test_the_limits_are_marked_provisional_where_defined():
    from squad import defaults
    assert defaults.PATH.read_text().count("PROVISIONAL") >= 2
    assert defaults.load()["claude_md"]["max_line_width"] == 300


def _mem(tmp_path):
    return tmp_path / "mem"


def test_setup_creates_the_memory_index_when_absent_and_never_overwrites_it(tmp_path):
    d, m = _repo(tmp_path), _mem(tmp_path)
    cli.main(["setup", "--target", str(d), "--apply", "--memory-dir", str(m)])
    assert (m / "MEMORY.md").is_file()
    (m / "MEMORY.md").write_text("- [mine](a.md) - keep\n")
    cli.main(["setup", "--target", str(d), "--apply", "--memory-dir", str(m)])
    assert (m / "MEMORY.md").read_text() == "- [mine](a.md) - keep\n"


def test_ledger_lines_are_proposed_and_written_only_with_approval(tmp_path, capsys):
    d, m = _repo(tmp_path), _mem(tmp_path)
    cli.main(["setup", "--target", str(d), "--apply", "--memory-dir", str(m)])
    p = d / "CLAUDE.md"
    p.write_text(p.read_text() + "\n- The audit ran on 2026-09-30 and found two defects.\n")
    capsys.readouterr()
    cli.main(["setup", "--target", str(d), "--apply", "--memory-dir", str(m)])
    out = capsys.readouterr().out
    assert "would propose" in out and not list(m.glob("project_*.md"))
    cli.main(["setup", "--target", str(d), "--apply", "--approve-memory", "--memory-dir", str(m)])
    (f,) = m.glob("project_*.md")
    assert "type: project" in f.read_text() and "2026-09-30" in f.read_text()
    assert f.name in (m / "MEMORY.md").read_text()
    assert "2026-09-30" in p.read_text()   # CLAUDE.md is the owner's to clean


def test_where_decisions_go_names_memory_for_a_one_off_fact_or_grant(tmp_path):
    d = _repo(tmp_path)
    cli.main(["setup", "--target", str(d), "--apply"])
    text = (d / "CLAUDE.md").read_text()
    assert "A one-off fact or grant: a memory file" in text.split("## Where decisions go")[1]

def test_this_repo_is_in_step_with_its_rule_adrs_and_holds_no_ledger(capsys):
    """ADR-0132: the suite runs `squad setup --check` on the repo itself."""
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    assert cli.main(["setup", "--target", str(root), "--check"]) == 0, capsys.readouterr().out


def test_a_ledger_line_or_a_missing_bullet_in_a_copy_of_this_repo_fails(tmp_path, capsys):
    import shutil
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    d = tmp_path / "copy"
    (d / "docs").mkdir(parents=True)
    shutil.copytree(root / "docs" / "adr", d / "docs" / "adr")
    shutil.copytree(root / "tests", d / "tests", ignore=shutil.ignore_patterns("__pycache__"))
    text = (root / "CLAUDE.md").read_text()
    (d / "CLAUDE.md").write_text(text + "\n- Ticket #5 is done.\n")
    assert cli.main(["setup", "--target", str(d), "--check"]) == 1
    assert "ticket status" in capsys.readouterr().out
    (d / "CLAUDE.md").write_text(text.replace("[ADR-0030]", "[ADR-0030x]"))
    assert cli.main(["setup", "--target", str(d), "--check"]) == 1


def test_apply_arms_the_commit_guard_so_a_local_slack_file_is_refused(tmp_path):
    """ADR-0177: setup is the tracked step that arms the guard; a fresh project refuses `slack.local.yaml` at once."""
    d = _repo(tmp_path)
    assert cli.main(["setup", "--target", str(d), "--apply"]) == 0
    hook = d / ".git" / "hooks" / "pre-commit"
    assert hook.is_file() and hook.stat().st_mode & 0o111

    (d / "slack.local.yaml").write_text("workspaces: []\n")
    (d / "notes.yaml").write_text("x: 1\n")
    git = ["git", "-C", str(d), "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run(git + ["add", "notes.yaml"], check=True)
    assert subprocess.run(git + ["commit", "-qm", "ordinary"]).returncode == 0
    subprocess.run(git + ["add", "slack.local.yaml"], check=True)
    assert subprocess.run(git + ["commit", "-qm", "leak"]).returncode != 0
    assert "leak" not in subprocess.run(git + ["log", "--oneline"], capture_output=True, text=True).stdout


def test_arming_is_reported_without_apply_and_skipped_outside_a_repository(tmp_path, capsys):
    d = tmp_path / "plain"
    d.mkdir()
    assert cli.main(["setup", "--target", str(d), "--apply"]) == 0
    assert not (d / ".git").exists()
    assert "commit guard" in capsys.readouterr().out
