"""Anthropic Claude provider. Map lỗi HTTP về 4 loại."""

from __future__ import annotations

import json
import urllib.request
import urllib.error
from typing import Any

from horae.llm.protocol import (
    LLMAuthError,
    LLMBadRequestError,
    LLMQuotaError,
    LLMTransientError,
    Message,
)
from horae.llm.providers._http import HttpPost
from horae.llm.registry import _register_provider


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, model: str, api_key: str, *, post: HttpPost | None = None):
        self.model = model
        self._api_key = api_key
        self._post = post

    def complete(self, messages: list[Message], *, max_tokens: int = 1000,
                 temperature: float = 0.0) -> str:
        url = "https://api.anthropic.com/v1/messages"
        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        body = json.dumps({
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        })
        status, data = self._do_post(url, headers, body)
        if status == 200:
            return data["content"][0]["text"]
        _raise_error(status, data, "anthropic")

    def _do_post(self, url, headers, body):
        if self._post is not None:
            return self._post(url, headers, body)
        return _default_post(url, headers, body)


def _default_post(url, headers, body):
    req = urllib.request.Request(url, data=body.encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return (resp.status, json.loads(resp.read().decode("utf-8")))
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8")
        try:
            return (exc.code, json.loads(text))
        except json.JSONDecodeError:
            return (exc.code, text)


def _raise_error(status: int, data: Any, provider: str):
    """Map HTTP status → đúng loại lỗi."""
    msg = str(data)[:200]
    if status == 401:
        raise LLMAuthError(f"{provider}: 401 — {msg}")
    if status == 403:
        raise LLMAuthError(f"{provider}: 403 — {msg}")
    if status == 429:
        raise LLMQuotaError(f"{provider}: 429 — {msg}")
    if 400 <= status < 500:
        raise LLMBadRequestError(f"{provider}: {status} — {msg}")
    if status >= 500:
        raise LLMTransientError(f"{provider}: {status} — {msg}")
    raise LLMTransientError(f"{provider}: unexpected status {status} — {msg}")


_register_provider("anthropic", lambda model, key, **kw: AnthropicProvider(model, key, **kw))
