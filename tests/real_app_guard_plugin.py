"""The `clean_app_start` fixture of #1415, in a plugin so a pytester run can load it."""
import pytest

from tests import real_app_guard as guard


@pytest.fixture
def clean_app_start():
    """A real-app case starts with no emulator alive (the evidence is the yielded snapshot) and leaves none behind."""
    seen = guard.clean_start()
    yield seen
    guard.reap(guard.reapable(guard.live_apps()))
