"""Issue #2912: the copy-id control still copies when the hub is served over plain http, and it says what happened.

A hub on plain http exposes no `navigator.clipboard`, so the control falls back to a hidden textarea and the legacy
copy command. The view draws its own state: "copied" on success and "copy failed" when neither path works. The seam is
the theme's page source, `hub.PAGE`, as in `tests/test_hub_slack_plain_words.py`.
"""
from tests import test_hub_slack_tree as T

hub = T.hub   # the shared module fixture, registered here under its own name


def test_the_copy_control_copies_without_the_clipboard_api(hub):
    page = hub.PAGE
    start = page.index("[data-slt-copy]")
    block = page[start:start + 1200]   # the copy handler and nothing past it
    assert "execCommand" in block, block
    assert "copy failed" in block, block
