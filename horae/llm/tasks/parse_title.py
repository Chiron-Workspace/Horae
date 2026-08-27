"""parse_title: regex trước, LLM sau, None→default. Không bao giờ raise."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Literal

from horae.adapters.parsing import regex_parse_title
from horae.llm.registry import LLMClient
from horae.settings import DEFAULT_ESTIMATE_MINUTES


@dataclass(frozen=True)
class ParsedTitle:
    """Kết quả parse tiêu đề task tự do."""

    kind: Literal["assignment", "ongoing"]
    estimate_minutes: int
    source: Literal["title", "description", "llm", "default"]
    cleaned_title: str


def _strip_json_fence(text: str) -> str:
    """Bỏ ```json ... ``` bọc quanh JSON."""
    m = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return text.strip()


def _parse_llm_json(text: str) -> dict | None:
    """Parse JSON từ LLM response. Ép chỉ trả JSON. Thất bại → None."""
    cleaned = _strip_json_fence(text)
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except (json.JSONDecodeError, ValueError):
        pass
    return None


def _default_result(raw: str) -> ParsedTitle:
    """Mặc định khi regex và LLM đều không có/không khả dụng."""
    return ParsedTitle(
        kind="assignment",
        estimate_minutes=DEFAULT_ESTIMATE_MINUTES,
        source="default",
        cleaned_title=raw,
    )


def parse_title(raw: str, llm: LLMClient | None) -> ParsedTitle:
    """1. Thử regex của parsing.py. Thành công → trả về ngay, KHÔNG gọi LLM.
       2. Regex thất bại VÀ llm khác None → hỏi LLM, ép trả JSON.
       3. llm là None HOẶC LLM lỗi → trả về mặc định. KHÔNG raise."""
    # 1. Regex
    result = regex_parse_title(raw)
    if result is not None:
        minutes, source, cleaned = result
        return ParsedTitle(
            kind="assignment",
            estimate_minutes=minutes,
            source=source,
            cleaned_title=cleaned,
        )

    # 2. LLM (nếu có)
    if llm is not None:
        try:
            from horae.llm.protocol import Message
            messages = [
                Message(role="system", content=(
                    "You are a task parser. Given a free-form task title, "
                    "extract the estimated minutes and return ONLY JSON "
                    '(no markdown, no explanation) with this exact shape: '
                    '{"minutes": <int>, "title": "<cleaned title>"}'
                )),
                Message(role="user", content=raw),
            ]
            response = llm.complete(messages, max_tokens=200, temperature=0.0)
            parsed = _parse_llm_json(response.text)
            if parsed is not None:
                minutes = parsed.get("minutes", 0)
                title = parsed.get("title", raw)
                if isinstance(minutes, int) and minutes > 0 and isinstance(title, str):
                    return ParsedTitle(
                        kind="assignment",
                        estimate_minutes=minutes,
                        source="llm",
                        cleaned_title=title,
                    )
        except Exception:
            pass  # LLM lỗi → về mặc định

    # 3. Mặc định
    return _default_result(raw)
