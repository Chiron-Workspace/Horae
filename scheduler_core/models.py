"""Các dataclass dữ liệu của scheduler. Thuần dữ liệu, không I/O, không đồng hồ hệ thống."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal, Optional


@dataclass(frozen=True)
class Interval:
    """Khoảng thời gian [start, end). start/end phải tz-aware và start < end."""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        for field_name, value in (("start", self.start), ("end", self.end)):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(
                    f"Interval.{field_name} phải là datetime tz-aware, nhận được naive: {value!r}"
                )
        if self.start >= self.end:
            raise ValueError(
                f"Interval yêu cầu start < end, nhận được start={self.start!r}, end={self.end!r}"
            )

    @property
    def duration_minutes(self) -> int:
        delta = self.end - self.start
        return int(delta.total_seconds() // 60)


@dataclass(frozen=True)
class FixedEvent:
    """Sự kiện cố định (lớp học, hẹn hò...) chiếm chỗ trong ngày."""

    title: str
    start: datetime
    end: datetime
    requires_travel: bool
    is_opaque: bool = True
    is_all_day: bool = False


@dataclass(frozen=True)
class Assignment:
    """Task có deadline, cần xếp vào các block làm việc."""

    task_id: str
    title: str
    estimate_minutes: int
    deadline: datetime
    done_minutes: int = 0

    @property
    def remaining_minutes(self) -> int:
        return max(0, self.estimate_minutes - self.done_minutes)


@dataclass(frozen=True)
class OngoingTask:
    """Task thường trực, mỗi ngày cần một lượng phút mục tiêu."""

    task_id: str
    title: str
    daily_target_minutes: int


@dataclass(frozen=True)
class Block:
    """Một block làm việc đã được xếp vào lịch."""

    task_id: str
    title: str
    start: datetime
    end: datetime
    kind: Literal["assignment", "ongoing"]

    @property
    def duration_minutes(self) -> int:
        delta = self.end - self.start
        return int(delta.total_seconds() // 60)


@dataclass(frozen=True)
class DayCapacity:
    """Kết quả tính năng suất một ngày sau khi trừ busy và áp ceiling."""

    date: date
    free_intervals: tuple[Interval, ...]
    busy_minutes: int
    ceiling_minutes: int
    capacity_minutes: int
    dropped_fragments: tuple[Interval, ...]
    leisure_interval: Interval | None


@dataclass(frozen=True)
class CheckResult:
    """Kết quả một bước kiểm tra (dùng cho sanity check ở part sau)."""

    name: str
    passed: bool
    detail: str
    severity: Literal["error", "warning"] = "error"
