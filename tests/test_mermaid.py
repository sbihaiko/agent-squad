"""Guards for ticket #51: the Mermaid view parses and does not drift from the fixture builder."""
import shutil
import subprocess
import sys

from squad.mermaid_check import check
from tests.helpers import DESIGN, five

MMD = (DESIGN / "five-phases.mmd").read_text()


def test_the_current_mermaid_file_parses_and_draws_every_node():
    assert check(MMD, [n["name"] for n in five()["nodes"]]) == []


def test_a_broken_mermaid_file_fails():
    assert check(MMD.replace("  end\n", "", 1))
    assert check(MMD.replace('scout["scout<br/>sonnet"]', 'scout["scout<br/>sonnet'))
    assert check(MMD + "  scout --> ghost\n")
    assert check(MMD.replace("flowchart TD", "graph"))
    assert check(MMD, ["not_a_node"]) == ["graph node not_a_node is not drawn"]


def test_the_committed_file_equals_what_the_builder_writes(tmp_path):
    shutil.copy(DESIGN / "build_fixture.py", tmp_path)
    subprocess.run([sys.executable, "build_fixture.py"], cwd=tmp_path, check=True, capture_output=True)
    assert (tmp_path / "five-phases.mmd").read_text() == MMD
