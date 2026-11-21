"""The phone page carries no voice control (#2490).

The mic, the recording row with its timer, waveform, cancel zone and hints, and the Kore clip of a spoken answer left the
phone page with the voice rule module. The page is the served phone page, composed the way the hub composes it.
"""
import pytest

from tests.test_hub_mobile_move import hub, run_page  # noqa: F401  (hub is the page fixture)


def page(hub_):
    return hub_.mobile_html().decode()


def test_the_phone_page_holds_no_mic_and_no_recording_row(hub):  # noqa: F811
    body = page(hub)
    for id_ in ("termview-mic", "termview-rec", "voice-time", "voice-wave", "voice-cancel", "voice-hints"):
        assert f'id="{id_}"' not in body, f"the phone page still holds #{id_}"
    assert "voicedot" not in body and "voicehint" not in body, "the phone page still styles the recording row"


def test_the_phone_page_loads_no_voice_rule_module(hub):  # noqa: F811
    body = page(hub)
    for name in ("voiceStart(", "voiceRow(", "voiceWave(", "voiceTick(", "voiceRelease(", "clipRow("):
        assert name not in body, f"the phone page still carries {name} from the voice module"


@pytest.mark.node_bound
def test_the_phone_page_asks_the_hub_for_no_clip(hub):  # noqa: F811
    out = run_page(hub.MOBILE_PAGE, "return Object.keys(__state.gets);")
    assert "/api/sessions/clip" not in out, out
