"""Fake in-memory cho test adapter/runner. Không gọi mạng."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Sequence
from zoneinfo import ZoneInfo

from horae.adapters.protocols import AutoBlock, BlockMeta, RawEvent, RawTask
from horae.adapters.gcal import WriterTargetMissing
from horae.settings import AUTO_BLOCK_PREFIX, AUTO_STUDY_CALENDAR, TODOIST_ID_PREFIX
from scheduler_core.config import SchedulerConfig
from scheduler_core.models import Block


# ---------------------------------------------------------------- fake HTTP


class FakeHttp:
    """Ghi lại mọi request. Trả response theo handler đã cài."""

    def __init__(self):
        self.calls: list[dict[str, Any]] = []
        self._handlers: dict[str, Any] = {}  # url_pattern -> response hoặc callable

    def add(self, url_contains: str, response: tuple[int, Any] | Any):
        """Cài response cho URL chứa url_contains. Có thể là (status, body) hoặc callable(url, headers, method, body)."""
        self._handlers[url_contains] = response

    def __call__(self, url: str, headers: dict[str, str], *, method: str = "GET", body: str | None = None) -> tuple[int, Any]:
        self.calls.append({"url": url, "headers": dict(headers), "method": method, "body": body})
        for pattern, resp in self._handlers.items():
            if pattern in url:
                if callable(resp):
                    return resp(url, headers, method, body)
                if isinstance(resp, tuple):
                    return resp
                return (200, resp)
        return (404, {"error": "no handler"})


# ---------------------------------------------------------------- fake source


class FakeTaskSource:
    """Nguồn task in-memory."""

    def __init__(self, tasks: Sequence[RawTask]):
        self._tasks = list(tasks)
        self.fetch_count = 0

    def fetch_open_tasks(self) -> Sequence[RawTask]:
        self.fetch_count += 1
        return tuple(self._tasks)


# ---------------------------------------------------------------- fake reader/writer


class FakeCalendarReader:
    """Reader in-memory. Trả event/auto_block đã cài sẵn."""

    def __init__(self, events: Sequence[RawEvent] | None = None,
                 auto_blocks: Sequence[AutoBlock] | None = None):
        self._events = list(events or [])
        self._auto_blocks = list(auto_blocks or [])

    def list_events(self, start: datetime, end: datetime) -> Sequence[RawEvent]:
        return tuple(e for e in self._events if start <= e.start < end)

    def list_auto_blocks(self, start: datetime, end: datetime) -> Sequence[AutoBlock]:
        return tuple(b for b in self._auto_blocks if start <= b.start < end)


class FakeCalendarWriter:
    """Writer in-memory. Persist create/delete state and never use the network."""

    target_calendar_name = AUTO_STUDY_CALENDAR

    def __init__(
        self,
        *,
        fail_on=None,
        has_auto_study=True,
        fail_all_after=False,
        auto_blocks=None,
        existing_blocks=None,
        calendar_id="auto-study",
        delete_fail_on=None,
        delete_fail_all_after=False,
    ):
        self.created = []  # historical successful/failed create outcomes are kept for old tests
        self.deleted = []
        self._counter = 0
        self._fail_on = fail_on
        self._has_auto_study = has_auto_study
        self._failed = False
        self._fail_all_after = fail_all_after
        self.target_calendar_id = calendar_id
        self._delete_fail_on = delete_fail_on
        self._delete_fail_all_after = delete_fail_all_after
        self._delete_failed = False
        self._state = list(auto_blocks or existing_blocks or [])

    def resolve_calendar_id(self):
        if not self._has_auto_study:
            raise WriterTargetMissing("Không có calendar Auto-Study")
        return self.target_calendar_id

    def create_block(self, block, meta):
        if not self._has_auto_study:
            raise WriterTargetMissing("Không có calendar Auto-Study")
        idx = len(self.created)
        if self._fail_on is not None and idx == self._fail_on and not self._failed:
            self._failed = True
            raise RuntimeError(f"Fake fail on block {idx}")
        if self._failed and self._fail_all_after:
            raise RuntimeError(f"Fake fail (persistent) on block {idx}")
        self._counter += 1
        event_id = f"fake-event-{self._counter}"
        self.created.append((block, meta, event_id))
        self._state.append(
            AutoBlock(
                event_id=event_id,
                task_id=meta.task_id,
                title=f"{AUTO_BLOCK_PREFIX} [{block.kind}] {block.title}",
                start=block.start,
                end=block.end,
                kind=block.kind,
                calendar_id=self.target_calendar_id,
                description=f"{TODOIST_ID_PREFIX} {meta.task_id}\nSource: {meta.source_id}",
                calendar_name=self.target_calendar_name,
            )
        )
        return event_id

    def delete_event(self, event_id, calendar_id):
        if not self._has_auto_study:
            raise WriterTargetMissing("Không có calendar Auto-Study")
        if calendar_id != self.target_calendar_id:
            raise RuntimeError(f"Fake wrong calendar: {calendar_id}")
        idx = len(self.deleted)
        if self._delete_fail_on is not None and idx == self._delete_fail_on and not self._delete_failed:
            self._delete_failed = True
            raise RuntimeError(f"Fake delete fail on event {event_id}")
        if self._delete_failed and self._delete_fail_all_after:
            raise RuntimeError(f"Fake delete fail (persistent) on event {event_id}")
        self.deleted.append(event_id)
        self._state = [block for block in self._state if block.event_id != event_id]

    def read_back(self, start: datetime, end: datetime) -> Sequence[AutoBlock]:
        return tuple(
            block for block in self._state
            if start <= block.start < end
        )


# ---------------------------------------------------------------- fake LLM client


@dataclass(frozen=True)
class FakeLLMResponse:
    """Bản sao tối giản của llm.registry.LLMResponse (chỉ cần .text)."""

    text: str
    provider_name: str = "fake"
    model: str = "fake-model"
    attempts: tuple[tuple[str, str], ...] = (("fake", "ok"),)


class FakeLLMClient:
    """LLMClient giả cho test parsing/runner. Đếm số lần được gọi.

    Đứng ở tầng LLMClient (cái mà parse_tasks/run nhận), không phải tầng
    provider: parse_title gọi ``llm.complete(...)`` rồi đọc ``.text``.
    Không có mạng, không đọc biến môi trường.
    """

    def __init__(
        self,
        *,
        response: str | None = None,
        minutes: int = 45,
        title: str = "Tiêu đề LLM dọn",
        kind: str = "assignment",
        error: Exception | None = None,
    ):
        if response is None:
            response = json.dumps(
                {"minutes": minutes, "title": title, "kind": kind}, ensure_ascii=False
            )
        self._response = response
        self._error = error
        self.call_count = 0
        self.calls: list[list[Any]] = []

    def complete(self, messages, **opts) -> FakeLLMResponse:
        self.call_count += 1
        self.calls.append(list(messages))
        if self._error is not None:
            raise self._error
        return FakeLLMResponse(text=self._response)
