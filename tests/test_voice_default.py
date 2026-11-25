"""GitHub issue #2973: Charon is the default voice of the daemon and of the voice helpers.

The owner chose Charon for HAL9K. A project persona names its own voice in `slack.yaml`; the DM and a channel with no
persona speak with the daemon's default, and `tts.py` and `reply.py` speak with theirs when no `--voice` is given.
"""
import ast
import pathlib

VOICE = pathlib.Path(__file__).resolve().parents[1] / "contrib" / "voice"


def _strings(name):
    return {n.value for n in ast.walk(ast.parse((VOICE / name).read_text())) if isinstance(n, ast.Constant)}


def test_the_daemon_default_voice_is_charon():
    tree = ast.parse((VOICE / "voiced.py").read_text())
    value = next(n.value.value for n in tree.body if isinstance(n, ast.Assign)
                 and any(getattr(t, "id", "") == "VOICE" for t in n.targets))
    assert value == "Charon"


def test_the_helpers_default_to_charon_and_name_no_other_voice():
    for name in ("tts.py", "reply.py"):
        assert "Charon" in _strings(name), name
        assert "Kore" not in _strings(name), name
