"""parse_title: regex trước, LLM sau, None→default."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from horae.adapters.parsing import regex_parse_title
from horae.llm.protocol import LLMError, LLMBadRequestError, Message
from horae.llm.registry import LLMClient
from horae.settings import DEFAULT_ESTIMATE_MINUTES
from horae.state.store import LocalStore


log = logging.getLogger(__name__)


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


def _cache_key(title: str, description: str) -> str:
    return hashlib.sha256(f"{title}|{description}".encode()).hexdigest()[:16]


def _parse_llm_json(text: str) -> dict | None:
    """Parse JSON từ LLM response. Ép chỉ trả JSON. Thất bại → None."""
    cleaned = _strip_json_fence(text)
    try:
        parsed = json.loads(cleaned)
        if not isinstance(parsed, dict):
            raise ValueError("JSON root không phải object")

        minutes = parsed["minutes"]
        title = parsed["title"]
        if type(minutes) is not int or minutes <= 0:
            raise ValueError("minutes phải là số nguyên dương")
        if not isinstance(title, str):
            raise ValueError("title phải là chuỗi")
        kind = parsed.get("kind", "assignment")
        if kind not in ("assignment", "ongoing"):
            raise ValueError("kind không hợp lệ")
        return parsed
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        log.warning(
            "parse_title: LLM trả JSON không hợp lệ (%s): %s",
            type(e).__name__,
            e,
        )
        return None


def _default_result(raw: str) -> ParsedTitle:
    """Mặc định khi regex và LLM đều không có/không khả dụng."""
    return ParsedTitle(
        kind="assignment",
        estimate_minutes=DEFAULT_ESTIMATE_MINUTES,
        source="default",
        cleaned_title=raw,
    )


def parse_title(
    raw: str,
    llm: LLMClient | None,
    description: str = "",
    store: LocalStore | None = None,
) -> ParsedTitle:
    """1. Thử regex của parsing.py. Thành công → trả về ngay, KHÔNG gọi LLM.
       2. Regex thất bại VÀ llm khác None → hỏi LLM, ép trả JSON.
       3. llm là None HOẶC LLM lỗi tạm thời → trả về mặc định.
          Lỗi bad request được phát ra để không che lỗi của caller.

    Cache chỉ được đọc/ghi khi có ``store`` và chỉ ghi sau khi JSON hợp lệ.
    """
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

    # 2. Cache theo cả tiêu đề và description, trước khi gọi LLM.
    key = _cache_key(raw, description)
    if store is not None:
        cached = store.load_llm_cache().get(key)
        if isinstance(cached, dict):
            minutes = cached.get("estimate_minutes")
            kind = cached.get("kind")
            if type(minutes) is int and minutes > 0 and kind in ("assignment", "ongoing"):
                return ParsedTitle(
                    kind=kind,
                    estimate_minutes=minutes,
                    source="llm",
                    cleaned_title=raw,
                )

    # 3. LLM (nếu có)
    if llm is not None:
        try:
            user_content = raw
            if description:
                user_content = f"Title: {raw}\nDescription: {description}"
            messages = [
                Message(role="system", content=(
                    "You are a task parser. Given a free-form task title, "
                    "extract the estimated minutes and return ONLY JSON "
                    '(no markdown, no explanation) with this exact shape: '
                    '{"minutes": <int>, "title": "<cleaned title>"}'
                )),
                Message(role="user", content=user_content),
            ]
            response = llm.complete(messages, max_tokens=4000, temperature=0.0)
            parsed = _parse_llm_json(response.text)
            if parsed is not None:
                parsed_result = ParsedTitle(
                    kind=parsed.get("kind", "assignment"),
                    estimate_minutes=parsed["minutes"],
                    source="llm",
                    cleaned_title=parsed["title"],
                )
                if store is not None:
                    cache = store.load_llm_cache()
                    cache[key] = {
                        "estimate_minutes": parsed_result.estimate_minutes,
                        "kind": parsed_result.kind,
                        "cached_at": datetime.now(timezone.utc).isoformat(),
                    }
                    store.save_llm_cache(cache)
                return parsed_result
        except LLMBadRequestError:
            raise
        except LLMError as e:
            log.warning(
                "parse_title: LLM không khả dụng (%s): %s",
                type(e).__name__,
                e,
            )

    # 4. Mặc định
    return _default_result(raw)
