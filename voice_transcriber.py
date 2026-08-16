"""Voice-message transcription (master prompt v2's voice-channel support): turns raw audio
bytes into text so a spoken renovation request flows through the exact same
`llm_parser.parse_renovation_request` pipeline as a typed one - `core/dialog_manager.py` has
no separate "voice" code path beyond transcribing first, then treating the result as if the
client had typed it.

Security note: `core/dialog_manager.py` downloads the audio bytes itself (server-side) before
calling `transcribe_fn` here - the Telegram file URL (which embeds the bot token, see
`messengers/telegram_adapter.py`) is never forwarded as-is to a third-party STT provider.

Testability: mirrors `llm_parser.py`'s `CompletionFn` injection pattern - `default_transcribe_fn`
is the only function in this module that actually calls a real speech-to-text provider; tests
inject a fake `transcribe_fn` instead.
"""

from __future__ import annotations

import os
from collections.abc import Callable

TranscribeFn = Callable[[bytes], str]
"""(raw audio bytes) -> transcribed text."""


def default_transcribe_fn(audio_bytes: bytes) -> str:
    """The only function in this module that actually calls a real speech-to-text provider.
    Provider comes purely from `TRANSCRIPTION_MODEL` (e.g. `groq/whisper-large-v3-turbo`),
    mirroring `llm_parser.default_completion_fn`'s `LLM_MODEL` convention - never hardcoded,
    never branched on in code.
    """
    import litellm  # imported lazily so this module is importable without the dependency

    model = os.environ.get("TRANSCRIPTION_MODEL")
    if not model:
        raise RuntimeError(
            "TRANSCRIPTION_MODEL environment variable is not set "
            "(e.g. 'groq/whisper-large-v3-turbo')"
        )
    # Telegram voice messages are OGG/Opus - the filename's extension is how most
    # OpenAI-compatible STT endpoints (incl. Groq's) detect the audio format.
    response = litellm.transcription(model=model, file=("voice.ogg", audio_bytes))
    text = response.text
    if not text:
        raise RuntimeError("Transcription provider returned empty text")
    return text
