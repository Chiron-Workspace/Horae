"""Cấu hình scheduler. Mọi con số điều chỉnh được đều nằm ở đây, không rải vào logic."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from typing import Literal
from zoneinfo import ZoneInfo


class ConfigError(ValueError):
    """Config không hợp lệ. Subclass của ValueError theo spec."""


@dataclass(frozen=True)
class DayWindow:
    start: time
    end: time


@dataclass(frozen=True)
class BreakWindow:
    name: str
    start: time
    end: time
    days: frozenset[int]  # 0=Mon..6=Sun; rỗng = mọi ngày


@dataclass(frozen=True)
class LeisureRule:
    minutes: int
    min_minutes: int
    days: frozenset[int]  # rỗng = mọi ngày (diễn giải đồng nhất với BreakWindow, xem NOTES.md)
    placement: Literal["latest", "earliest"] = "latest"


@dataclass(frozen=True)
class TravelRule:
    pre_minutes: int
    post_minutes: int
    recovery_minutes: int
    chain_gap_threshold: int


@dataclass(frozen=True)
class CeilingRule:
    busy_threshold_minutes: int
    ceiling_busy: int
    ceiling_free: int


@dataclass(frozen=True)
class BlockRule:
    min_minutes: int
    default_minutes: int
    max_minutes: int
    same_task_gap: int
    switch_task_gap: int
    allocation_unit: int
    round_start_to: int | None


def _minutes_of_day(t: time) -> int:
    return t.hour * 60 + t.minute


def _window_minutes(w: DayWindow) -> int:
    return _minutes_of_day(w.end) - _minutes_of_day(w.start)


def _time_to_str(t: time) -> str:
    return t.strftime("%H:%M")


def _time_from_str(s: str) -> time:
    return time.fromisoformat(s)


@dataclass(frozen=True)
class SchedulerConfig:
    timezone: str
    day_windows: dict[int, DayWindow]  # thiếu thứ nào = thứ đó không xếp gì
    breaks: tuple[BreakWindow, ...]
    leisure: LeisureRule | None
    travel: TravelRule
    ceiling: CeilingRule
    blocks: BlockRule
    min_fragment_minutes: int
    default_estimate_minutes: int
    small_task_threshold: int
    small_task_deadline_days: int
    default_deadline_time: time
    planning_horizon_days: int
    write_horizon_days: int

    # ------------------------------------------------------------------ validate

    def validate(self) -> None:
        """Kiểm tra 11 luật trong spec. Raise ConfigError (nêu trường và giá trị) khi vi phạm."""
        # 1. timezone phải là IANA hợp lệ
        try:
            ZoneInfo(self.timezone)
        except (ValueError, KeyError) as exc:
            raise ConfigError(f"timezone không phải IANA hợp lệ: {self.timezone!r}") from exc

        # 2. DayWindow nào có start >= end
        for day, window in sorted(self.day_windows.items()):
            if window.start >= window.end:
                raise ConfigError(
                    f"day_windows[{day}]: start ({_time_to_str(window.start)}) phải nhỏ hơn "
                    f"end ({_time_to_str(window.end)})"
                )

        # 3 + 4. break nằm trong DayWindow của ngày áp dụng, và các break cùng ngày không chồng lấn
        for day in range(7):
            applicable = [b for b in self.breaks if not b.days or day in b.days]
            window = self.day_windows.get(day)
            for b in applicable:
                if b.start >= b.end:
                    raise ConfigError(
                        f"break {b.name!r} (ngày {day}): start ({_time_to_str(b.start)}) "
                        f"phải nhỏ hơn end ({_time_to_str(b.end)})"
                    )
                if window is not None and (b.start < window.start or b.end > window.end):
                    raise ConfigError(
                        f"break {b.name!r} ({_time_to_str(b.start)}–{_time_to_str(b.end)}) nằm ngoài "
                        f"day_windows[{day}] ({_time_to_str(window.start)}–{_time_to_str(window.end)})"
                    )
            ordered = sorted(applicable, key=lambda b: b.start)
            for first, second in zip(ordered, ordered[1:]):
                if second.start < first.end:
                    raise ConfigError(
                        f"break {first.name!r} ({_time_to_str(first.start)}–{_time_to_str(first.end)}) "
                        f"chồng lấn break {second.name!r} "
                        f"({_time_to_str(second.start)}–{_time_to_str(second.end)}) vào ngày {day}"
                    )

        # 5. blocks: min <= default <= max
        blocks = self.blocks
        if blocks.min_minutes > blocks.default_minutes:
            raise ConfigError(
                f"blocks.min_minutes ({blocks.min_minutes}) phải <= "
                f"blocks.default_minutes ({blocks.default_minutes})"
            )
        if blocks.default_minutes > blocks.max_minutes:
            raise ConfigError(
                f"blocks.default_minutes ({blocks.default_minutes}) phải <= "
                f"blocks.max_minutes ({blocks.max_minutes})"
            )

        # 6. allocation_unit dương và chia hết min_minutes
        if blocks.allocation_unit <= 0:
            raise ConfigError(f"blocks.allocation_unit ({blocks.allocation_unit}) phải > 0")
        if blocks.min_minutes % blocks.allocation_unit != 0:
            raise ConfigError(
                f"blocks.min_minutes ({blocks.min_minutes}) phải chia hết cho "
                f"blocks.allocation_unit ({blocks.allocation_unit})"
            )

        # 7 + 8. leisure
        if self.leisure is not None:
            leisure = self.leisure
            if leisure.min_minutes > leisure.minutes:
                raise ConfigError(
                    f"leisure.min_minutes ({leisure.min_minutes}) phải <= "
                    f"leisure.minutes ({leisure.minutes})"
                )
            leisure_days = leisure.days if leisure.days else frozenset(range(7))
            for day in sorted(leisure_days):
                window = self.day_windows.get(day)
                if window is not None and leisure.minutes > _window_minutes(window):
                    raise ConfigError(
                        f"leisure.minutes ({leisure.minutes}) lớn hơn tổng thời lượng "
                        f"day_windows[{day}] ({_window_minutes(window)} phút, "
                        f"{_time_to_str(window.start)}–{_time_to_str(window.end)})"
                    )

        # 9. travel không được âm
        for field_name in ("pre_minutes", "post_minutes", "recovery_minutes", "chain_gap_threshold"):
            value = getattr(self.travel, field_name)
            if value < 0:
                raise ConfigError(f"travel.{field_name} không được âm, nhận được {value}")

        # 10. ceiling_busy <= ceiling_free
        if self.ceiling.ceiling_busy > self.ceiling.ceiling_free:
            raise ConfigError(
                f"ceiling.ceiling_busy ({self.ceiling.ceiling_busy}) phải <= "
                f"ceiling.ceiling_free ({self.ceiling.ceiling_free})"
            )

        # 11. min_fragment_minutes <= blocks.min_minutes
        if self.min_fragment_minutes > blocks.min_minutes:
            raise ConfigError(
                f"min_fragment_minutes ({self.min_fragment_minutes}) phải <= "
                f"blocks.min_minutes ({blocks.min_minutes})"
            )

        # 12. chain_gap_threshold phải > pre + post (luật chuỗi phải có tác dụng)
        pre_post = self.travel.pre_minutes + self.travel.post_minutes
        if self.travel.chain_gap_threshold <= pre_post:
            raise ConfigError(
                f"travel.chain_gap_threshold ({self.travel.chain_gap_threshold}) phải > "
                f"travel.pre_minutes + travel.post_minutes "
                f"({self.travel.pre_minutes} + {self.travel.post_minutes} = {pre_post}); "
                f"luật chuỗi sẽ không có tác dụng khi threshold <= pre + post"
            )

    # ------------------------------------------------------------ (de)serialize

    def to_dict(self) -> dict:
        """JSON thuần: time -> "HH:MM", frozenset[int] -> list đã sort, None giữ nguyên."""
        leisure_dict = None
        if self.leisure is not None:
            leisure_dict = {
                "minutes": self.leisure.minutes,
                "min_minutes": self.leisure.min_minutes,
                "days": sorted(self.leisure.days),
                "placement": self.leisure.placement,
            }
        return {
            "timezone": self.timezone,
            "day_windows": {
                str(day): {"start": _time_to_str(w.start), "end": _time_to_str(w.end)}
                for day, w in sorted(self.day_windows.items())
            },
            "breaks": [
                {
                    "name": b.name,
                    "start": _time_to_str(b.start),
                    "end": _time_to_str(b.end),
                    "days": sorted(b.days),
                }
                for b in self.breaks
            ],
            "leisure": leisure_dict,
            "travel": {
                "pre_minutes": self.travel.pre_minutes,
                "post_minutes": self.travel.post_minutes,
                "recovery_minutes": self.travel.recovery_minutes,
                "chain_gap_threshold": self.travel.chain_gap_threshold,
            },
            "ceiling": {
                "busy_threshold_minutes": self.ceiling.busy_threshold_minutes,
                "ceiling_busy": self.ceiling.ceiling_busy,
                "ceiling_free": self.ceiling.ceiling_free,
            },
            "blocks": {
                "min_minutes": self.blocks.min_minutes,
                "default_minutes": self.blocks.default_minutes,
                "max_minutes": self.blocks.max_minutes,
                "same_task_gap": self.blocks.same_task_gap,
                "switch_task_gap": self.blocks.switch_task_gap,
                "allocation_unit": self.blocks.allocation_unit,
                "round_start_to": self.blocks.round_start_to,
            },
            "min_fragment_minutes": self.min_fragment_minutes,
            "default_estimate_minutes": self.default_estimate_minutes,
            "small_task_threshold": self.small_task_threshold,
            "small_task_deadline_days": self.small_task_deadline_days,
            "default_deadline_time": _time_to_str(self.default_deadline_time),
            "planning_horizon_days": self.planning_horizon_days,
            "write_horizon_days": self.write_horizon_days,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SchedulerConfig":
        leisure = None
        if d["leisure"] is not None:
            leisure = LeisureRule(
                minutes=d["leisure"]["minutes"],
                min_minutes=d["leisure"]["min_minutes"],
                days=frozenset(d["leisure"]["days"]),
                placement=d["leisure"]["placement"],
            )
        return cls(
            timezone=d["timezone"],
            day_windows={
                int(day): DayWindow(
                    start=_time_from_str(w["start"]),
                    end=_time_from_str(w["end"]),
                )
                for day, w in d["day_windows"].items()
            },
            breaks=tuple(
                BreakWindow(
                    name=b["name"],
                    start=_time_from_str(b["start"]),
                    end=_time_from_str(b["end"]),
                    days=frozenset(b["days"]),
                )
                for b in d["breaks"]
            ),
            leisure=leisure,
            travel=TravelRule(
                pre_minutes=d["travel"]["pre_minutes"],
                post_minutes=d["travel"]["post_minutes"],
                recovery_minutes=d["travel"]["recovery_minutes"],
                chain_gap_threshold=d["travel"]["chain_gap_threshold"],
            ),
            ceiling=CeilingRule(
                busy_threshold_minutes=d["ceiling"]["busy_threshold_minutes"],
                ceiling_busy=d["ceiling"]["ceiling_busy"],
                ceiling_free=d["ceiling"]["ceiling_free"],
            ),
            blocks=BlockRule(
                min_minutes=d["blocks"]["min_minutes"],
                default_minutes=d["blocks"]["default_minutes"],
                max_minutes=d["blocks"]["max_minutes"],
                same_task_gap=d["blocks"]["same_task_gap"],
                switch_task_gap=d["blocks"]["switch_task_gap"],
                allocation_unit=d["blocks"]["allocation_unit"],
                round_start_to=d["blocks"]["round_start_to"],
            ),
            min_fragment_minutes=d["min_fragment_minutes"],
            default_estimate_minutes=d["default_estimate_minutes"],
            small_task_threshold=d["small_task_threshold"],
            small_task_deadline_days=d["small_task_deadline_days"],
            default_deadline_time=_time_from_str(d["default_deadline_time"]),
            planning_horizon_days=d["planning_horizon_days"],
            write_horizon_days=d["write_horizon_days"],
        )


def _full_week(start: time, end: time) -> dict[int, DayWindow]:
    return {day: DayWindow(start, end) for day in range(7)}


# ---------------------------------------------------------------------- presets

DEFAULT_CONFIG = SchedulerConfig(
    timezone="UTC",
    day_windows=_full_week(time(7, 0), time(22, 0)),
    breaks=(BreakWindow("lunch", time(12, 0), time(13, 0), frozenset()),),
    leisure=None,
    travel=TravelRule(pre_minutes=30, post_minutes=30, recovery_minutes=0, chain_gap_threshold=120),
    ceiling=CeilingRule(busy_threshold_minutes=240, ceiling_busy=240, ceiling_free=360),
    blocks=BlockRule(
        min_minutes=30,
        default_minutes=60,
        max_minutes=90,
        same_task_gap=10,
        switch_task_gap=20,
        allocation_unit=15,
        round_start_to=30,
    ),
    min_fragment_minutes=30,
    default_estimate_minutes=60,
    small_task_threshold=90,
    small_task_deadline_days=7,
    default_deadline_time=time(21, 0),
    planning_horizon_days=14,
    write_horizon_days=2,
)

PRESET_STUDENT_VN = SchedulerConfig(
    timezone="Asia/Ho_Chi_Minh",
    day_windows=_full_week(time(7, 0), time(22, 0)),
    breaks=(
        BreakWindow("lunch", time(12, 0), time(14, 0), frozenset()),
        BreakWindow("dinner", time(19, 0), time(20, 0), frozenset()),
    ),
    leisure=LeisureRule(minutes=180, min_minutes=90, days=frozenset({5, 6}), placement="latest"),
    travel=TravelRule(pre_minutes=120, post_minutes=75, recovery_minutes=30, chain_gap_threshold=210),
    ceiling=CeilingRule(busy_threshold_minutes=240, ceiling_busy=240, ceiling_free=360),
    blocks=BlockRule(
        min_minutes=30,
        default_minutes=60,
        max_minutes=90,
        same_task_gap=10,
        switch_task_gap=20,
        allocation_unit=15,
        round_start_to=30,
    ),
    min_fragment_minutes=30,
    default_estimate_minutes=60,
    small_task_threshold=90,
    small_task_deadline_days=7,
    default_deadline_time=time(21, 0),
    planning_horizon_days=14,
    write_horizon_days=2,
)
