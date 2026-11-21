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


def test_a_path_root_resolves_as_its_string_does(hub, tmp_path):
    """The hub's CHECKOUT is a Path wherever a caller sets one, and the resolving path named the roots by joining them: a
    Path among them raised TypeError before any root was tried, so the operator panel counted nothing."""
    caps, _ = hub.load_squad([tmp_path, ROOT])
    assert pathlib.Path(caps.__file__).resolve().is_relative_to(ROOT.resolve()), caps.__file__
    with pytest.raises(ModuleNotFoundError):
        hub.load_squad([tmp_path])


def _package(root, who):
    pkg = root / "squad"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    for name in ("caps", "run_caps"):
        (pkg / f"{name}.py").write_text(f"WHO = {who!r}\n")
    return root


def test_a_held_copy_under_a_later_root_never_answers_for_an_earlier_root(hub):
    """#2475: the copy this interpreter already holds answers only under the FIRST root that holds a package. The live tree's
    parent is `~/.claude`, which contains the plugin cache's old copy, and that copy answered for the checkout that stood
    first, so every process read failed."""
    import importlib
    import sys

    before = _ambient()
    with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
        _package(pathlib.Path(a), "first")
        _package(pathlib.Path(b), "later")
        sys.path.insert(0, b)   # the later root's copy is the one this process holds, the plugin cache's place
        try:
            importlib.import_module("squad.caps")
            caps, _ = hub.load_squad([a, b])
            assert caps.WHO == "first", "the held copy under the later root answered for the earlier root"
            assert pathlib.Path(caps.__file__).resolve().is_relative_to(pathlib.Path(a).resolve()), caps.__file__
        finally:
            sys.path.remove(b)
            for m in [m for m in sys.modules if m == "squad" or m.startswith("squad.")]:
                sys.modules.pop(m, None)
            sys.modules.update(before)   # the interpreter's own package is the one it held, as the resolution leaves it


def test_a_refused_root_is_not_left_on_sys_path(hub, tmp_path):
    """The refused root goes back off sys.path, so the next root imports clean (a root that stayed would shadow it)."""
    import sys

    with pytest.raises(ModuleNotFoundError):
        hub.load_squad([str(tmp_path)])
    assert str(tmp_path) not in sys.path


def _ambient():
    import sys

    return {m: mod for m, mod in sys.modules.items() if m == "squad" or m.startswith("squad.")}


def test_a_refused_root_leaves_the_callers_own_package_as_it_was(hub, tmp_path):
    """The resolution is one call's business: the caller keeps one `squad` module and one class per name.
    A purge that stayed parted every class the caller already held. In the full gate at 5531c10 a test that imports this
    hub into its own interpreter lost the identity between the `ResumeRefused` a loaded `launch` raises and the one
    `pytest.raises` holds, and a monkeypatched `defaults.PATH` landed on a module the reimported `launch` never read: two
    unrelated tests failed, in one worker, for a reason neither of them sets up (#453)."""
    import sys

    before = _ambient()
    assert before, "the premise: this process holds a squad package"
    with pytest.raises(ModuleNotFoundError):
        hub.load_squad([str(tmp_path)])
    for m, mod in before.items():
        assert sys.modules.get(m) is mod, m


@pytest.mark.heavy
def test_a_concurrent_import_never_breaks_the_resolution(hub):
    """The hub serves on threads, so `load_squad` purges and reads `sys.modules` while another thread imports.

    A `sys.modules` iterated while a key is added raises "dictionary changed size during iteration". The live hub read
    that as a resolution that failed while a profile loaded, so the page it served stayed shut. The resolution takes its
    snapshots and holds one lock, so a concurrent import never reaches it.
    """
    import threading

    stop = threading.Event()
    failures, rounds = [], 100

    def churn():
        while not stop.is_set():
            importlib.import_module("squad.caps")   # a second thread adds keys while the resolution reads them

    def resolve():
        for _ in range(rounds):
            try:
                caps, _ = hub.load_squad([str(ROOT)])
                assert pathlib.Path(caps.__file__).resolve().is_relative_to(ROOT.resolve())
            except Exception as e:   # noqa: BLE001 - the point is that nothing but a refusal may come out
                failures.append(e)
                return

    t = threading.Thread(target=churn, daemon=True)
    t.start()
    try:
        workers = [threading.Thread(target=resolve) for _ in range(4)]
        for w in workers:
            w.start()
        for w in workers:
            w.join()
    finally:
        stop.set()
        t.join()
    assert not failures, failures


def test_an_answering_root_leaves_the_callers_own_package_as_it_was(hub):
    import sys

    before = _ambient()
    caps, _ = hub.load_squad([str(ROOT)])
    assert pathlib.Path(caps.__file__).resolve().is_relative_to(ROOT.resolve()), caps.__file__
    for m, mod in before.items():
        assert sys.modules.get(m) is mod, m
