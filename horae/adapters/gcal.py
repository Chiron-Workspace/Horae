"""Adapter Google Calendar. Đọc token env, bỏ qua calendar bị loại, ghi vào Auto-Study."""

from __future__ import annotations

import json
import os
import re
import warnings
from datetime import date, datetime, time, timedelta
from typing import Any, Callable, Protocol, Sequence
from zoneinfo import ZoneInfo

from horae.adapters.protocols import AutoBlock, BlockMeta, RawEvent
from horae.settings import AUTO_BLOCK_PREFIX, AUTO_STUDY_CALENDAR, IGNORED_CALENDARS, TODOIST_ID_PREFIX
from scheduler_core.config import SchedulerConfig
from scheduler_core.models import Block, FixedEvent


class HttpFetch(Protocol):
    def __call__(self, url: str, headers: dict[str, str], *, method: str = "GET", body: str | None = None) -> tuple[int, Any]: ...


def _default_fetch(url: str, headers: dict[str, str], *, method: str = "GET", body: str | None = None) -> tuple[int, Any]:
    import urllib.request

    req = urllib.request.Request(url, headers=headers, method=method)
    if body is not None:
        req.data = body.encode("utf-8")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            text = resp.read().decode("utf-8")
            try:
                return (resp.status, json.loads(text))
            except json.JSONDecodeError:
                return (resp.status, text)
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8")
        try:
            return (exc.code, json.loads(text))
        except json.JSONDecodeError:
            return (exc.code, text)


class WriterTargetMissing(RuntimeError):
    """Không tìm thấy calendar Auto-Study để ghi."""


# ---------------------------------------------------------------- helpers


def _detect_travel(text: str, task_id: str) -> tuple[bool, list[str]]:
    """Trả (requires_travel, warnings). 'Offline' → True, 'Online' → False, không → False + cảnh báo."""
    warns: list[str] = []
    if not text:
        warns.append(f"event: không có Offline/Online trong description, mặc định requires_travel=False")
        return (False, warns)
    low = text.lower()
    if "offline" in low:
        return (True, warns)
    if "online" in low:
        return (False, warns)
    warns.append(f"event: không có Offline/Online trong description, mặc định requires_travel=False")
    return (False, warns)


def _parse_gcal_datetime(dt_obj: dict, tz: ZoneInfo) -> datetime:
    """GCal: {"dateTime": "2026-01-05T19:00:00+07:00"} hoặc {"date": "2026-01-05"}."""
    if "dateTime" in dt_obj:
        return datetime.fromisoformat(dt_obj["dateTime"])
    if "date" in dt_obj:
        d = date.fromisoformat(dt_obj["date"])
        return datetime.combine(d, time(0, 0), tzinfo=tz)
    raise ValueError(f"Không parse được datetime GCal: {dt_obj}")


def _is_all_day(dt_obj: dict) -> bool:
    return "date" in dt_obj and "dateTime" not in dt_obj


def _extract_task_id(description: str) -> str | None:
    """Lấy Todoist task ID khỏi description: 'Todoist task ID: 12345'."""
    if not description:
        return None
    m = re.search(r"Todoist task ID:\s*(\S+)", description)
    if m:
        return m.group(1)
    return None


def _extract_kind(title: str) -> str:
    """Tiêu đề block [Auto] có thể chứa marker loại. Mặc định 'assignment'."""
    # Format: "[Auto] [assignment] Title" hoặc "[Auto] [ongoing] Title"
    m = re.search(r"\[(assignment|ongoing)\]", title, re.IGNORECASE)
    if m:
        return m.group(1).lower()
    return "assignment"


# ---------------------------------------------------------------- reader


class GoogleCalendarReader:
    """Đọc event GCal. list_events gọi MỘT lần cho cả khoảng, nhóm theo ngày trong memory."""

    BASE_URL = "https://www.googleapis.com/calendar/v3"

    def __init__(self, config: SchedulerConfig, token_env: str = "GCAL_TOKEN", *, fetch: HttpFetch | None = None):
        self._config = config
        self._tz = ZoneInfo(config.timezone)
        self._token_env = token_env
        self._fetch = fetch if fetch is not None else _default_fetch

    def _headers(self) -> dict[str, str]:
        token = os.environ.get(self._token_env)
        if not token:
            raise RuntimeError(f"Biến môi trường {self._token_env} không tồn tại hoặc rỗng")
        return {"Authorization": f"Bearer {token}"}

    def _list_calendars(self) -> list[dict]:
        """Lấy calendar list, bỏ qua IGNORED_CALENDARS."""
        url = f"{self.BASE_URL}/users/me/calendarList"
        status, data = self._fetch(url, self._headers())
        if status != 200:
            raise RuntimeError(f"GCal calendarList failed: status={status}")
        cals = data.get("items", []) if isinstance(data, dict) else []
        return [c for c in cals if c.get("summary", "") not in IGNORED_CALENDARS]

    def list_events(self, start: datetime, end: datetime) -> Sequence[RawEvent]:
        """Gọi MỘT lần cho cả khoảng [start, end) trên mọi calendar (trừ bị loại)."""
        cals = self._list_calendars()
        time_min = start.astimezone(self._tz).isoformat()
        time_max = end.astimezone(self._tz).isoformat()
        all_events: list[RawEvent] = []
        for cal in cals:
            cal_id = cal.get("id", "")
            url = (
                f"{self.BASE_URL}/calendars/{cal_id}/events"
                f"?timeMin={time_min}&timeMax={time_max}&singleEvents=true&orderBy=startTime"
            )
            status, data = self._fetch(url, self._headers())
            if status != 200:
                continue
            items = data.get("items", []) if isinstance(data, dict) else []
            for ev in items:
                start_obj = ev.get("start", {})
                end_obj = ev.get("end", {})
                all_day = _is_all_day(start_obj)
                try:
                    ev_start = _parse_gcal_datetime(start_obj, self._tz)
                    ev_end = _parse_gcal_datetime(end_obj, self._tz)
                except (ValueError, KeyError):
                    continue
                transparency = ev.get("transparency", "opaque")
                is_opaque = transparency != "transparent"
                all_events.append(
                    RawEvent(
                        event_id=ev.get("id", ""),
                        title=ev.get("summary", ""),
                        start=ev_start,
                        end=ev_end,
                        description=ev.get("description", "") or "",
                        is_opaque=is_opaque,
                        is_all_day=all_day,
                        calendar_id=cal_id,
                        calendar_name=cal.get("summary", ""),
                    )
                )
        return tuple(all_events)

    def list_auto_blocks(self, start: datetime, end: datetime) -> Sequence[AutoBlock]:
        """Lấy block [Auto] trong khoảng — lọc theo tiền tố tiêu đề."""
        events = self.list_events(start, end)
        blocks: list[AutoBlock] = []
        for ev in events:
            if not ev.title.startswith(AUTO_BLOCK_PREFIX):
                continue
            task_id = _extract_task_id(ev.description)
            if task_id is None:
                warnings.warn(f"AutoBlock {ev.event_id} thiếu task ID trong description, bỏ qua")
                continue
            kind = _extract_kind(ev.title)
            blocks.append(
                AutoBlock(
                    event_id=ev.event_id,
                    task_id=task_id,
                    title=ev.title,
                    start=ev.start,
                    end=ev.end,
                    kind=kind,  # type: ignore[arg-type]
                    calendar_id=ev.calendar_id,
                    description=ev.description,
                    calendar_name=ev.calendar_name,
                )
            )
        return tuple(blocks)

    def to_fixed_events(self, raw_events: Sequence[RawEvent]) -> list[FixedEvent]:
        """Quy đổi RawEvent → FixedEvent cho scheduler_core."""
        warns: list[str] = []
        fixed: list[FixedEvent] = []
        for ev in raw_events:
            if ev.is_all_day or not ev.is_opaque:
                continue
            travel, w = _detect_travel(ev.description, ev.event_id)
            warns.extend(w)
            fixed.append(
                FixedEvent(
                    title=ev.title,
                    start=ev.start.astimezone(self._tz) if ev.start.tzinfo else ev.start.replace(tzinfo=self._tz),
                    end=ev.end.astimezone(self._tz) if ev.end.tzinfo else ev.end.replace(tzinfo=self._tz),
                    requires_travel=travel,
                    is_opaque=ev.is_opaque,
                    is_all_day=ev.is_all_day,
                )
            )
        return fixed


# ---------------------------------------------------------------- writer


class GoogleCalendarWriter:
    """Ghi/xóa block trong calendar Auto-Study, không tự tạo calendar."""

    BASE_URL = "https://www.googleapis.com/calendar/v3"

    def __init__(self, config: SchedulerConfig, token_env: str = "GCAL_TOKEN", *, fetch: HttpFetch | None = None):
        self._config = config
        self._tz = ZoneInfo(config.timezone)
        self._token_env = token_env
        self._fetch = fetch if fetch is not None else _default_fetch
        self._auto_study_id: str | None = None

    @property
    def target_calendar_name(self) -> str:
        return AUTO_STUDY_CALENDAR

    def _headers(self) -> dict[str, str]:
        token = os.environ.get(self._token_env)
        if not token:
            raise RuntimeError(f"Biến môi trường {self._token_env} không tồn tại hoặc rỗng")
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def _resolve_calendar(self) -> str:
        """Tìm calendar Auto-Study. Không thấy → raise WriterTargetMissing."""
        if self._auto_study_id is not None:
            return self._auto_study_id
        url = f"{self.BASE_URL}/users/me/calendarList"
        status, data = self._fetch(url, self._headers())
        if status != 200:
            raise RuntimeError(f"GCal calendarList failed: status={status}")
        cals = data.get("items", []) if isinstance(data, dict) else []
        for cal in cals:
            if cal.get("summary", "") == AUTO_STUDY_CALENDAR:
                calendar_id = cal.get("id", "")
                if calendar_id:
                    self._auto_study_id = calendar_id
                    return calendar_id
        raise WriterTargetMissing(
            f"Không tìm thấy calendar '{AUTO_STUDY_CALENDAR}' trong danh sách calendar"
        )

    def resolve_calendar_id(self) -> str:
        """Resolve the exact Auto-Study calendar without creating a fallback."""
        return self._resolve_calendar()

    def create_block(self, block: Block, meta: BlockMeta) -> str:
        """Tạo event GCal cho block. Trả event id."""
        cal_id = self._resolve_calendar()
        url = f"{self.BASE_URL}/calendars/{cal_id}/events"
        kind_marker = f"[{block.kind}]"
        title = f"{AUTO_BLOCK_PREFIX} {kind_marker} {block.title}"
        description = f"{TODOIST_ID_PREFIX} {meta.task_id}\nSource: {meta.source_id}"
        body = json.dumps({
            "summary": title,
            "description": description,
            "start": {"dateTime": block.start.astimezone(self._tz).isoformat()},
            "end": {"dateTime": block.end.astimezone(self._tz).isoformat()},
        })
        status, data = self._fetch(url, self._headers(), method="POST", body=body)
        if status not in (200, 201):
            raise RuntimeError(f"GCal create failed: status={status}, body={data}")
        event_id = data.get("id", "") if isinstance(data, dict) else ""
        return event_id

    def delete_event(self, event_id: str, calendar_id: str) -> None:
        """Delete exactly one event from the already resolved Auto-Study calendar."""
        if not event_id:
            raise ValueError("event_id không được rỗng")
        resolved_id = self._resolve_calendar()
        if not calendar_id or calendar_id != resolved_id:
            raise ValueError(
                f"delete target calendar_id '{calendar_id}' không phải Auto-Study '{resolved_id}'"
            )
        url = f"{self.BASE_URL}/calendars/{calendar_id}/events/{event_id}"
        status, data = self._fetch(url, self._headers(), method="DELETE")
        if status not in (200, 204):
            raise RuntimeError(f"GCal delete failed: status={status}, body={data}")

    def read_back(self, start: datetime, end: datetime) -> Sequence[AutoBlock]:
        """Đọc lại block [Auto] trong khoảng — dùng reader."""
        reader = GoogleCalendarReader(self._config, self._token_env, fetch=self._fetch)
        return reader.list_auto_blocks(start, end)
