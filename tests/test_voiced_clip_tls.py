"""Guard: issue #2933. The clip download and the Gemini transcription trust the same CA bundle the Socket Mode
connection does, and a failed clip names the step that failed and the reason, never a token, a key or a URL query.

The seam is the module's one HTTPS call: a stub opener stands in for `urllib.request.urlopen`, and the record and the
log line are the daemon's own files. No case reaches Slack, Gemini or the network.
"""
import importlib.util
import io
import json
import sys
from pathlib import Path
from urllib.error import URLError

from tests.test_voiced import OWNER, clip_event, lines

ROOT = Path(__file__).resolve().parent.parent
VOICE_DIR = ROOT / "contrib" / "voice"
if str(VOICE_DIR) not in sys.path:
    sys.path.insert(0, str(VOICE_DIR))


def voiced():
    """Load the daemon fresh under its own module name, so one test's imports never answer another's."""
    spec = importlib.util.spec_from_file_location("voiced", VOICE_DIR / "voiced.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StubOpener:
    """One stub `urlopen`: it records the arguments and answers with the bytes the case names."""

    def __init__(self, answer=b"", error=None):
        self.answer = answer
        self.error = error
        self.calls = []

    def __call__(self, request, **kwargs):
        self.calls.append({"request": request, "kwargs": kwargs})
        if self.error is not None:
            raise self.error
        return io.BytesIO(self.answer)


def trusted_context(mod, monkeypatch, name="the-daemons-context"):
    """The one context `ssl_context()` builds, as a sentinel the case can find on the call."""
    monkeypatch.setattr(mod, "ssl_context", lambda log=None: name)
    return name


def test_download_gets_the_context_the_daemon_trusts(tmp_path, monkeypatch):
    """Criterion (1): the clip download passes the context from `ssl_context()` to its one HTTPS call."""
    mod = voiced()
    context = trusted_context(mod, monkeypatch)
    opener = StubOpener(b"clip-bytes")
    monkeypatch.setattr(mod.urllib.request, "urlopen", opener)

    assert mod.download("https://files.slack.com/files-pri/x", "xoxb-token") == b"clip-bytes"
    assert opener.calls[0]["kwargs"].get("context") == context, "the download did not trust the daemon's CA bundle"


def test_transcribe_gets_the_context_the_daemon_trusts(tmp_path, monkeypatch):
    """Criterion (2): the Gemini call passes the context from `ssl_context()` to its one HTTPS call."""
    mod = voiced()
    context = trusted_context(mod, monkeypatch)
    answer = {"candidates": [{"content": {"parts": [{"text": "the transcript"}]}}]}
    opener = StubOpener(json.dumps(answer).encode())
    monkeypatch.setattr(mod.urllib.request, "urlopen", opener)

    assert mod.transcribe(b"audio", "audio/mp4", "AIza-key") == "the transcript"
    assert opener.calls[0]["kwargs"].get("context") == context, "the transcription did not trust the daemon's CA bundle"


def daemon_with(tmp_path, mod, downloader=None, transcriber=None, log=None):
    """One daemon over its own state, with the failing download or transcription the case names."""
    return mod.Daemon(state=mod.State(str(tmp_path)), owner=OWNER,
                      transcriber=transcriber or (lambda audio, mime: "a transcript"),
                      downloader=downloader or (lambda url: b"audio"),
                      log=(log if log is not None else []).append)


def test_a_failed_download_logs_the_step_and_the_reason(tmp_path):
    """Criterion (3): a failed clip names the step `download` and the reason of the `URLError`."""
    mod = voiced()
    log = []

    def downloader(url):
        raise URLError(OSError("CERTIFICATE_VERIFY_FAILED for files.slack.com"))

    daemon = daemon_with(tmp_path, mod, downloader=downloader, log=log)
    daemon.handle(clip_event())

    assert any("download" in line and "CERTIFICATE_VERIFY_FAILED" in line for line in log), log
    assert not any("transcrib" in line for line in log), log


def test_a_failed_transcription_logs_the_step_and_the_reason(tmp_path):
    """Criterion (4): a failed clip names the step `transcribe` and the reason of the `URLError`."""
    mod = voiced()
    log = []

    def transcriber(audio, mime):
        raise URLError(OSError("CERTIFICATE_VERIFY_FAILED for generativelanguage.googleapis.com"))

    daemon = daemon_with(tmp_path, mod, transcriber=transcriber, log=log)
    daemon.handle(clip_event())

    assert any("transcribe" in line and "CERTIFICATE_VERIFY_FAILED" in line and "?" not in line for line in log), log


def test_a_urlerror_reason_never_carries_a_key_or_a_url_query_into_the_log(tmp_path):
    """Criterion (5): the reason is kept, the key and the query it carries are not."""
    mod = voiced()
    log = []
    key = "AIza-SECRET-GEMINI-KEY"
    query = "X-Amz-Signature-SECRET-QUERY"
    reason = f"CERTIFICATE_VERIFY_FAILED at https://generativelanguage.googleapis.com/x?key={key}&sig={query}"

    def transcriber(audio, mime):
        raise URLError(OSError(reason))

    daemon = daemon_with(tmp_path, mod, transcriber=transcriber, log=log)
    daemon.handle(clip_event())

    text = "\n".join(log)
    assert "CERTIFICATE_VERIFY_FAILED" in text, log
    assert key not in text, "a log line holds the key"
    assert query not in text, "a log line holds the URL query"


def test_a_log_line_redacts_a_url_path_token_and_a_bare_key_prefix(tmp_path):
    """Criterion (5): a token in a URL path, or a bare key with a known prefix, never enters the log line.

    The query is not the only place a secret hides: a provider message can carry the whole URL, whose path holds the
    token, or the key alone with no `key=` before it. The reason is kept, the URL and the key are not."""
    mod = voiced()
    log = []
    path_token = "xoxb-PATH-SECRET"
    bare_key = "AIzaBARE-SECRET"
    reason = f"CERTIFICATE_VERIFY_FAILED for https://files.slack.com/files-pri/{path_token} and {bare_key}"

    def downloader(url):
        raise URLError(OSError(reason))

    daemon = daemon_with(tmp_path, mod, downloader=downloader, log=log)
    daemon.handle(clip_event())

    text = "\n".join(log)
    assert "CERTIFICATE_VERIFY_FAILED" in text, log
    assert path_token not in text, log
    assert bare_key not in text, log
    assert "https://" not in text, "a log line holds a whole URL, so a token in its path can ride with it"


def test_the_record_names_the_step_that_failed(tmp_path):
    """The record the operator reads names the same step, so a clip that never transcribed is not read as one that
    never downloaded."""
    mod = voiced()
    log = []

    def transcriber(audio, mime):
        raise URLError(OSError("boom"))

    daemon = daemon_with(tmp_path, mod, transcriber=transcriber, log=log)
    daemon.handle(clip_event())

    other = lines(daemon.state.other_path)
    assert len(other) == 1, other
    assert "transcri" in other[0]["error"], other
