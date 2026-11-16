"""Guard for #1972: the warn-only ASD-STE100 check reads an issue body, prints its findings, and never blocks."""
import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check_ste.py"
spec = importlib.util.spec_from_file_location("check_ste", SCRIPT)
C = importlib.util.module_from_spec(spec)
spec.loader.exec_module(C)

FILL = ["the", "of", "and", "to", "in", "a"]


def sentence(n, lead):
    """A sentence of n words: the lead word, then function words that form no noun stack."""
    words = [lead] + [FILL[i % len(FILL)] for i in range(n - 1)]
    return " ".join(words) + "."


def kinds(findings):
    return [f.kind for f in findings]


def run_cli(*args, stdin=""):
    return subprocess.run([sys.executable, str(SCRIPT), *args], input=stdin, capture_output=True, text=True)


def test_instruction_over_twenty_words_is_a_strict_finding():
    body = "## Acceptance criteria\n\n- [ ] " + sentence(21, "Run") + "\n"
    findings = C.check(body)
    assert kinds(findings) == ["instruction-length"]
    assert findings[0].section == "Acceptance criteria"
    assert findings[0].sentence == sentence(21, "Run")


def test_instruction_of_twenty_words_passes():
    assert C.check("## Acceptance criteria\n\n- [ ] " + sentence(20, "Run") + "\n") == []


def test_description_over_twenty_five_words_is_a_strict_finding():
    body = "## Acceptance criteria\n\n- [ ] " + sentence(26, "The") + "\n"
    assert kinds(C.check(body)) == ["description-length"]


def test_description_of_twenty_five_words_passes():
    assert C.check("## Acceptance criteria\n\n- [ ] " + sentence(25, "The") + "\n") == []


def test_numbered_steps_are_strict_instructions():
    body = "## Steps\n\n1. " + sentence(21, "Run") + "\n"
    assert kinds(C.check(body)) == ["instruction-length"]


def test_observed_and_expected_are_strict_sections():
    body = "## Observed\n\n" + sentence(26, "The") + "\n\n## Expected\n\n" + sentence(26, "The") + "\n"
    assert [f.section for f in C.check(body)] == ["Observed", "Expected"]


def test_light_section_has_no_word_limit():
    assert C.check("## Solution\n\n" + sentence(60, "The") + "\n") == []


def test_passive_voice_in_a_light_section_is_a_finding():
    findings = C.check("## Problem Statement\n\nThe run record is written by the loop.\n")
    assert kinds(findings) == ["passive-voice"]
    assert findings[0].detail == "is written"


def test_passive_voice_in_a_strict_section_is_a_finding():
    findings = C.check("## Acceptance criteria\n\n- [ ] The record is written to the run folder.\n")
    assert kinds(findings) == ["passive-voice"]


def test_active_sentence_passes():
    assert C.check("## Solution\n\nThe loop writes the run record to the run folder.\n") == []


def test_noun_stack_of_four_is_a_light_finding():
    findings = C.check("## Solution\n\nThe squad board card state changes after each publish.\n")
    assert kinds(findings) == ["noun-stack"]
    assert findings[0].detail == "squad board card state"


def test_noun_stack_of_three_passes():
    assert C.check("## Solution\n\nThe card is in the squad board.\n") == []


def test_title_over_twelve_words_is_a_finding():
    findings = C.check("", title=sentence(13, "Run")[:-1])
    assert kinds(findings) == ["title-length"]
    assert findings[0].section == "Title"


def test_title_of_twelve_words_passes():
    assert C.check("", title=sentence(12, "Run")[:-1]) == []


def test_code_span_and_fenced_code_are_not_read():
    body = (
        "## Solution\n\n"
        "Run `the record is written by the loop` now.\n\n"
        "```text\nThe record is written by the loop.\n```\n"
    )
    assert C.check(body) == []


def test_cli_reads_a_file_and_prints_one_line_per_finding(tmp_path):
    body = tmp_path / "body.md"
    body.write_text("## Solution\n\nThe record is written by the loop.\n\n## Observed\n\n" + sentence(26, "The") + "\n")
    r = run_cli("--title", "Short title", str(body))
    assert r.returncode == 0
    lines = r.stdout.splitlines()
    assert len(lines) == 2
    assert lines[0].startswith("Solution | passive-voice | is written | ")
    assert lines[1].startswith("Observed | description-length | ")


def test_cli_reads_stdin():
    r = run_cli(stdin="## Solution\n\nThe record is written by the loop.\n")
    assert r.returncode == 0
    assert len(r.stdout.splitlines()) == 1


def test_cli_prints_nothing_for_a_clean_body():
    r = run_cli(stdin="## Solution\n\nThe loop writes the run record.\n")
    assert r.returncode == 0
    assert r.stdout == ""


def test_cli_usage_errors_exit_two(tmp_path):
    assert run_cli(str(tmp_path / "missing.md")).returncode == 2
    assert run_cli("--no-such-option").returncode == 2


def test_the_check_has_no_network_or_process_import():
    assert not {"subprocess", "urllib", "socket", "http"} & set(vars(C))
