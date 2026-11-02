from squad.redact import MARK, redact, safe_cut


def test_key_shaped_tokens_are_masked():
    text = "use sk-ant-api03-abcdefghijklmnopqrstu and ghp_abcdefghijklmnopqrstuvwxyz0123456789"
    out = redact(text)
    assert "sk-ant" not in out
    assert "ghp_" not in out
    assert out.count(MARK) == 2


def test_assignment_keeps_its_name_and_masks_its_value():
    assert redact("password=hunter2 ok") == f"password={MARK} ok"
    assert redact("API_KEY: abc123xyz") == f"API_KEY: {MARK}"


def test_ordinary_text_is_unchanged():
    text = "the tokenizer splits a sentence into words"
    assert redact(text) == text


def test_cut_stops_after_the_last_whitespace():
    assert safe_cut(b"one two sk-ant-") == 8


def test_cut_holds_a_tail_with_no_whitespace():
    assert safe_cut(b"no-space-yet") == 0


def test_cut_releases_a_tail_past_the_hold():
    assert safe_cut(b"x" * 3000) == 3000


def test_cut_releases_everything_once_final():
    assert safe_cut(b"no-space-yet", final=True) == len(b"no-space-yet")
