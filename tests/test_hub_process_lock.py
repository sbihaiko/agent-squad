"""One resolution lock per process, not one per hub.py instance.

hubmux.py loads hub.py once per profile into one interpreter, so each profile's module carries its own `_SQUAD_LOCK`: the
lock then serializes nothing across profiles while every instance purges and imports the same `sys.modules`. The live hub
read that race as a resolution that failed at start, and as "the process reading failed" on the run list.
"""
import importlib.util
import os
import pathlib
import tempfile
import threading

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
HUB_PY = ROOT / "contrib/squad-goal/scripts/hub.py"


@pytest.fixture
def two_hubs():
    """Two hub.py instances, as hubmux.py loads two profiles: separate module objects, one interpreter."""
    os.environ.setdefault("SG_REPO", "x/y")
    os.environ.setdefault("SG_OWNER", "x")
    os.environ.setdefault("SG_PROJECT", "4")
    os.environ.setdefault("SG_CHECKOUT", str(ROOT))
    os.environ.setdefault("SG_HUB_DIR", tempfile.mkdtemp())
    mods = []
    for name in ("hub_lock_alpha", "hub_lock_beta"):
        spec = importlib.util.spec_from_file_location(name, HUB_PY)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mods.append(mod)
    return mods


def test_every_hub_instance_shares_the_one_process_lock(two_hubs):
    """hubmux.py loads hub.py once per profile; a module-level lock leaves one lock per profile and none between them."""
    alpha, beta = two_hubs
    assert alpha._SQUAD_LOCK is beta._SQUAD_LOCK


@pytest.mark.heavy
def test_a_resolution_never_fails_while_another_profiles_hub_resolves(two_hubs):
    """One instance's purge reaches another instance's import only while both hold the same lock."""
    alpha, beta = two_hubs
    failures = []
    start = threading.Barrier(4)

    def resolve(mod):
        start.wait()
        for _ in range(150):
            try:
                (caps,) = mod.load_squad([str(ROOT)], ("caps",))
                assert pathlib.Path(caps.__file__).resolve().is_relative_to(ROOT.resolve())
            except Exception as e:  # noqa: BLE001 - the point is that nothing but an answer may come out
                failures.append(f"{mod.__name__}: {type(e).__name__}: {e}")
                return

    workers = [threading.Thread(target=resolve, args=(mod,)) for mod in (alpha, beta) for _ in range(2)]
    for w in workers:
        w.start()
    for w in workers:
        w.join()
    assert not failures, failures
