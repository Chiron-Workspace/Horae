"""DeepSeek provider. Cùng API shape OpenAI, endpoint khác."""

from __future__ import annotations

import json
from typing import Any

from horae.llm.protocol import Message
from horae.llm.providers.anthropic import _default_post, _raise_error
from horae.llm.providers._http import HttpPost
from horae.llm.registry import _register_provider


class DeepSeekProvider:
    name = "deepseek"

    def __init__(self, model: str, api_key: str, *, post: HttpPost | None = None):
        self.model = model
        self._api_key = api_key
        self._post = post

    def complete(self, messages: list[Message], *, max_tokens: int = 1000,
                 temperature: float = 0.0) -> str:
        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "content-type": "application/json",
        }
        body = json.dumps({
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        })
        if self._post is not None:
            status, data = self._post(url, headers, body)
        else:
            status, data = _default_post(url, headers, body)
        if status == 200:
            return data["choices"][0]["message"]["content"]
        _raise_error(status, data, "deepseek")


_register_provider("deepseek", lambda model, key, **kw: DeepSeekProvider(model, key, **kw))
