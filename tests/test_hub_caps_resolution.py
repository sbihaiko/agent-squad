"""The hub reads the machine-wide caps from the root it resolved, never from whatever the interpreter can import (#452).

The footer's ceiling comes from `squad.caps` of the profile's checkout. Under `uv run` -- which is how every gate runs the
suites -- the checkout's own `.venv` is first on PATH and holds `squad` as an editable install, so an import for a root that
holds no package at all still found one, and `test-hub-caps.sh` read a ceiling where it asserts that nothing resolves. The
same suite failed in the full gate at c3c6274 and passed alone: one environment apart, the same tree.
"""
import importlib.util
import os
import pathlib
import tempfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"


@pytest.fixture(scope="module")
def hub():
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ.setdefault("SG_CHECKOUT", str(ROOT))
    os.environ["SG_HUB_DIR"] = tempfile.mkdtemp()
    spec = importlib.util.spec_from_file_location("hub_caps_resolution", HUB_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_package_is_importable_from_this_test_process():
    """The premise: `squad` is importable here whatever root is asked for, so a resolution that only tries the import cannot
    tell an empty root from a populated one."""
    import squad

    assert pathlib.Path(squad.__file__).is_file(), squad.__file__


def test_a_root_holding_no_package_is_refused_though_squad_imports(hub, tmp_path):
    with pytest.raises(ModuleNotFoundError) as e:
        hub.load_squad([str(tmp_path)])
    assert str(tmp_path) in str(e.value) and "holds no squad package" in str(e.value), str(e.value)


def test_the_root_that_holds_the_package_is_taken(hub):
    caps, _ = hub.load_squad([str(ROOT)])
    assert pathlib.Path(caps.__file__).resolve().is_relative_to(ROOT.resolve()), caps.__file__


def test_an_empty_root_before_the_real_one_is_skipped(hub, tmp_path):
    caps, _ = hub.load_squad([str(tmp_path), str(ROOT)])
    assert pathlib.Path(caps.__file__).resolve().is_relative_to(ROOT.resolve()), caps.__file__


def test_a_refused_root_is_not_left_on_sys_path(hub, tmp_path):
    """The refused root goes back off sys.path, so the next root imports clean (a root that stayed would shadow it)."""
    import sys

    with pytest.raises(ModuleNotFoundError):
        hub.load_squad([str(tmp_path)])
    assert str(tmp_path) not in sys.path
