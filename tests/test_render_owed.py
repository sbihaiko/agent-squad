"""A child of an issue that carries an "after" image owes a render (#2833): the loop reads the issue body and the
child's worktree, never the child's status word. The predicate is pure, so each body shape is one case here."""
import importlib.util
import subprocess
import sys
from pathlib import Path

MOD = Path(__file__).resolve().parent.parent / "contrib/squad-goal/scripts/render_owed.py"


def _mod():
    spec = importlib.util.spec_from_file_location("render_owed", MOD)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _render(wt, name="screen.png"):
    d = wt / ".scratch" / "renders"
    (d / name).parent.mkdir(parents=True, exist_ok=True)
    (d / name).write_bytes(b"\x89PNG\r\n")


def test_a_markdown_after_image_owes_a_render(tmp_path):
    m = _mod()
    body = "## Expected\n\n![after](https://github.com/user-attachments/assets/1a2b.png)\n"
    assert m.render_owed(body, tmp_path) is True


def test_a_markdown_image_named_after_by_its_file_owes_a_render(tmp_path):
    m = _mod()
    assert m.render_owed("![screen](https://x.test/img/after-communication.png)", tmp_path) is True


def test_an_img_tag_with_after_alt_text_owes_a_render(tmp_path):
    m = _mod()
    body = '<img width="900" alt="After: the Communication page" src="https://x.test/assets/9f.png" />'
    assert m.render_owed(body, tmp_path) is True


def test_an_img_tag_named_after_by_its_file_owes_a_render(tmp_path):
    m = _mod()
    assert m.render_owed('<img src="https://x.test/a/After_hub.png">', tmp_path) is True


def test_an_image_under_an_after_heading_owes_a_render(tmp_path):
    m = _mod()
    body = "## Before\n\n![Image](https://x.test/1.png)\n\n## After\n\n![Image](https://x.test/2.png)\n"
    assert m.render_owed(body, tmp_path) is True


def test_an_image_after_a_plain_after_line_owes_a_render(tmp_path):
    m = _mod()
    body = "After the change, the page reads:\n\n<img alt=\"Image\" src=\"https://x.test/3.png\">\n"
    assert m.render_owed(body, tmp_path) is True


def test_a_before_only_body_owes_no_render(tmp_path):
    m = _mod()
    body = "## Before\n\n![before](https://x.test/before.png)\n<img alt=\"Image\" src=\"https://x.test/b.png\">\n"
    assert m.render_owed(body, tmp_path) is False


def test_a_body_with_no_image_owes_no_render(tmp_path):
    m = _mod()
    body = "## After\n\nThe page shows one row per run. See after.png in the spec.\n"
    assert m.render_owed(body, tmp_path) is False


def test_an_empty_body_owes_no_render(tmp_path):
    m = _mod()
    assert m.render_owed("", tmp_path) is False
    assert m.render_owed(None, tmp_path) is False


def test_a_png_under_the_render_folder_settles_the_render(tmp_path):
    m = _mod()
    _render(tmp_path / "wt", "nested/communication.png")
    assert m.render_owed("![after](https://x.test/a.png)", tmp_path / "wt") is False


def test_a_file_that_is_no_png_is_no_render_evidence(tmp_path):
    m = _mod()
    d = tmp_path / ".scratch" / "renders"
    d.mkdir(parents=True)
    (d / "notes.txt").write_text("rendered later")
    assert m.render_owed("![after](https://x.test/a.png)", tmp_path) is True


def test_a_png_outside_the_render_folder_is_no_render_evidence(tmp_path):
    m = _mod()
    (tmp_path / ".scratch").mkdir()
    (tmp_path / ".scratch" / "shot.png").write_bytes(b"\x89PNG")
    assert m.render_owed("![after](https://x.test/a.png)", tmp_path) is True


def test_the_review_is_a_changes_review_in_the_shipped_shape_with_a_repro(tmp_path):
    m = _mod()
    text = m.render_review()
    review = tmp_path / "review.md"
    review.write_text(text)
    shape = subprocess.run([sys.executable, str(MOD.parent / "revformat.py"), str(review)], capture_output=True, text=True)
    assert shape.returncode == 0, shape.stdout + shape.stderr
    assert text.splitlines()[0] == "VERDICT: CHANGES"
    blocking = [line for line in text.splitlines() if line.startswith("- ")]
    assert len(blocking) == 1
    assert "repro: " in blocking[0]
    assert ".scratch/renders/" in blocking[0]
    assert "test-hub-e2e.py" in blocking[0]
    assert "ego-browser" in blocking[0]


def test_the_repro_exits_non_zero_until_a_render_exists(tmp_path):
    m = _mod()
    repro = m.render_review().split("repro: ", 1)[1].strip()
    assert subprocess.run(["sh", "-c", repro], cwd=tmp_path).returncode != 0
    _render(tmp_path)
    assert subprocess.run(["sh", "-c", repro], cwd=tmp_path).returncode == 0
