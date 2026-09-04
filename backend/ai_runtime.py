"""Runtime shim for the LLM provider.

The original code imported ``emergentintegrations`` at module import time, so the
whole API refused to boot when the package (or the key) was missing.  This module
makes AI strictly optional: every feature that needs it fails with a clear
``AIUnavailable`` error (mapped to HTTP 503) instead of crashing the server.
"""
import os
import logging

logger = logging.getLogger(__name__)

EMERGENT_LLM_KEY = os.environ.get("EMERGENT_LLM_KEY")
DEFAULT_MODEL = ("openai", os.environ.get("LLM_MODEL", "gpt-5.4-mini"))


class AIUnavailable(Exception):
    """Raised when an AI-backed feature is used without a configured provider."""


try:  # pragma: no cover - exercised only when the vendor package is installed
    from emergentintegrations.llm.chat import LlmChat, UserMessage, ImageContent, TextDelta, StreamDone  # type: ignore
    from emergentintegrations.llm.openai import OpenAISpeechToText  # type: ignore
    AI_PACKAGE_AVAILABLE = True
except Exception:  # ImportError or a broken native dependency
    AI_PACKAGE_AVAILABLE = False

    class _Unavailable:
        def __init__(self, *args, **kwargs):
            raise AIUnavailable(
                "Οι λειτουργίες AI δεν είναι διαθέσιμες: δεν έχει εγκατασταθεί ο πάροχος LLM "
                "ή λείπει το EMERGENT_LLM_KEY."
            )

    class _Never:  # isinstance() checks against stream events must simply be False
        pass

    LlmChat = UserMessage = ImageContent = OpenAISpeechToText = _Unavailable  # type: ignore
    TextDelta = StreamDone = _Never  # type: ignore


def ai_ready() -> bool:
    return AI_PACKAGE_AVAILABLE and bool(EMERGENT_LLM_KEY)


async def run_chat(system_message: str, prompt: str, image_base64: str | None = None,
                   session_prefix: str = "chat") -> str:
    """Single-turn helper used by the automation engine (OCR of emailed receipts)."""
    if not ai_ready():
        raise AIUnavailable("AI provider not configured")
    import uuid
    chat = LlmChat(api_key=EMERGENT_LLM_KEY, session_id=f"{session_prefix}-{uuid.uuid4()}",
                   system_message=system_message).with_model(*DEFAULT_MODEL)
    if image_base64:
        msg = UserMessage(text=prompt, file_contents=[ImageContent(image_base64=image_base64)])
    else:
        msg = UserMessage(text=prompt)
    full_text = ""
    async for ev in chat.stream_message(msg):
        if isinstance(ev, TextDelta):
            full_text += ev.content
        elif isinstance(ev, StreamDone):
            break
    return full_text
