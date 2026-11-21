"""The served phone page carries every client module the page names.

The page names its client modules as placeholders and hub.py fills them at boot, so a module the page names and the hub
never inlines ships a dead screen. Two waves that add a screen to the same fill chain merge into one line, and a merge
that keeps one side drops the other's fill: each module's text is in the served page, and its placeholder is gone
(ADR-0137: the rule lives in the module, and the page is a view).
"""
import tests.hub_mobile_stub as move
from tests.hub_mobile_stub import hub  # noqa: F401  (hub is a fixture)

MODULES = (
    ("hub-mobile-loop.js", "LOOP_RULES"),
    ("hub-mobile-usage.js", "USAGE"),
    ("hub-mobile-specs.js", "SPECS"),
)


def test_the_served_page_inlines_every_client_module_it_names(hub):  # noqa: F811
    for name, token in MODULES:
        assert (move.SCRIPTS / name).read_text() in hub.MOBILE_PAGE, f"the page names {name} and the hub never inlines it"
        assert f"/*{token}*/" not in hub.MOBILE_PAGE, f"the page keeps the {token} placeholder, so the screen is dead"
