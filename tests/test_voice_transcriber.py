"""Tests for `voice_transcriber.py`. NEVER makes a live STT call - `default_transcribe_fn`'s
one real network dependency (`litellm.transcription`) is monkeypatched, per the same
"mock the LiteLLM call" convention already used by `tests/test_llm_parser.py`.
"""

from __future__ import annotations

import pytest

from voice_transcriber import default_transcribe_fn


def test_default_transcribe_fn_requires_transcription_model_env_var(monkeypatch):
    # `litellm` auto-loads `.env` on its own first import (a real repo `.env` may set
    # TRANSCRIPTION_MODEL) - import it here first so that side effect already happened before
    # we delete the var, otherwise `default_transcribe_fn`'s own lazy `import litellm` would
    # silently re-populate it from disk and this test would call a real provider instead.
    import litellm  # noqa: F401

    monkeypatch.delenv("TRANSCRIPTION_MODEL", raising=False)

    with pytest.raises(RuntimeError, match="TRANSCRIPTION_MODEL"):
        default_transcribe_fn(b"fake-audio-bytes")


def test_default_transcribe_fn_calls_litellm_transcription_and_returns_text(monkeypatch):
    import litellm

    captured = {}

    class _FakeResponse:
        text = "remont \u0142azienki"

    def _fake_transcription(model, file):
        captured["model"] = model
        captured["file"] = file
        return _FakeResponse()

    monkeypatch.setenv("TRANSCRIPTION_MODEL", "groq/whisper-large-v3-turbo")
    monkeypatch.setattr(litellm, "transcription", _fake_transcription)

    result = default_transcribe_fn(b"fake-audio-bytes")

    assert result == "remont \u0142azienki"
    assert captured["model"] == "groq/whisper-large-v3-turbo"
    assert captured["file"] == ("voice.ogg", b"fake-audio-bytes")


def test_default_transcribe_fn_raises_on_empty_transcription(monkeypatch):
    import litellm

    class _EmptyResponse:
        text = ""

    monkeypatch.setenv("TRANSCRIPTION_MODEL", "groq/whisper-large-v3-turbo")
    monkeypatch.setattr(litellm, "transcription", lambda model, file: _EmptyResponse())

    with pytest.raises(RuntimeError, match="empty text"):
        default_transcribe_fn(b"fake-audio-bytes")
