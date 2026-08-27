"""Protocol cho các nguồn task và calendar. Dùng dataclass, không dict trần."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal, Protocol, Sequence

from scheduler_core.models import Block


# ---------------------------------------------------------------- raw types


@dataclass(frozen=True)
class RawTask:
    """Task thô từ nguồn (Todoist...). Chưa phân loại."""

    task_id: str
    title: str
    description: str
    labels: tuple[str, ...]
    due: datetime | date | None  # Todoist có thể chỉ có ngày, có thể có giờ


@dataclass(frozen=True)
class RawEvent:
    """Sự kiện calendar thô, chưa quy đổi về FixedEvent."""

    event_id: str
    title: str
    start: datetime
    end: datetime
    description: str
    is_opaque: bool
    is_all_day: bool
    calendar_id: str
    calendar_name: str = ""


@dataclass(frozen=True)
class AutoBlock:
    """Block [Auto] đã có trên calendar — dùng cho ledger và chống trùng."""

    event_id: str
    task_id: str
    title: str
    start: datetime
    end: datetime
    kind: Literal["assignment", "ongoing"]
    calendar_id: str
    description: str = ""
    calendar_name: str = ""


@dataclass(frozen=True)
class BlockMeta:
    """Metadata đi kèm khi tạo block: gắn task ID gốc vào description."""

    task_id: str
    source_id: str  # Todoist task ID (để ledger khớp lại)


# ---------------------------------------------------------------- protocols


class TaskSource(Protocol):
    def fetch_open_tasks(self) -> Sequence[RawTask]: ...


class CalendarReader(Protocol):
    def list_events(self, start: datetime, end: datetime) -> Sequence[RawEvent]: ...
    def list_auto_blocks(self, start: datetime, end: datetime) -> Sequence[AutoBlock]: ...


class CalendarWriter(Protocol):
    """Writer phải chỉ rõ target và xóa từng event, không xóa theo range."""

    target_calendar_name: str

    def resolve_calendar_id(self) -> str: ...
    def create_block(self, block: Block, meta: BlockMeta) -> str: ...
    def delete_event(self, event_id: str, calendar_id: str) -> None: ...
    def read_back(self, start: datetime, end: datetime) -> Sequence[AutoBlock]: ...
