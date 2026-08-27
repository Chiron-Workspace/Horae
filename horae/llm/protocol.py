"""Protocol và lỗi cho LLM provider. Mỗi provider phải map lỗi HTTP về 4 loại."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Message:
    """Tin nhắn chat. role = "user" | "system" | "assistant"."""

    role: str
    content: str


# ---------------------------------------------------------------- errors


class LLMError(Exception):
    """Lỗi chung cho mọi provider."""


class LLMAuthError(LLMError):
    """Key sai / hết hạn. → fallback sang provider kế tiếp."""


class LLMQuotaError(LLMError):
    """Hết quota / rate limit. → fallback."""


class LLMTransientError(LLMError):
    """5xx, timeout, mạng. → fallback."""


class LLMBadRequestError(LLMError):
    """Prompt sai → LỖI CỦA TA. → DỪNG NGAY, không fallback."""


# ---------------------------------------------------------------- protocol


class LLMProvider(Protocol):
    name: str
    model: str

    def complete(self, messages: list[Message], *, max_tokens: int = 1000,
                 temperature: float = 0.0) -> str: ...
