"""Tính sức chứa một ngày: trừ event, break, đệm di chuyển, leisure, mảnh vụn, cutoff deadline."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Sequence
from zoneinfo import ZoneInfo

from scheduler_core.config import SchedulerConfig
from scheduler_core.intervals import (
    clip,
    drop_shorter_than,
    find_contiguous_block,
    longest_interval,
    normalize,
    subtract,
    total_minutes,
)
from scheduler_core.models import DayCapacity, FixedEvent, Interval


def _interval_on_day(day: date, start: time, end: time, tz: ZoneInfo) -> Interval:
    return Interval(
        datetime.combine(day, start, tzinfo=tz),
        datetime.combine(day, end, tzinfo=tz),
    )


def _build_chains(
    events: list[FixedEvent], threshold_minutes: int
) -> list[list[FixedEvent]]:
    """Gom các event travel (đã sort theo start) thành chuỗi.

    Hai event liên tiếp thuộc cùng chuỗi khi (sau.start - trước.end) < threshold
    (strict `<`). Bắc cầu theo thứ tự sort.
    """
    threshold = timedelta(minutes=threshold_minutes)
    chains: list[list[FixedEvent]] = []
    current: list[FixedEvent] = []
    for event in events:
        if not current:
            current = [event]
            continue
        previous = current[-1]
        if (event.start - previous.end) < threshold:
            current.append(event)
        else:
            chains.append(current)
            current = [event]
    if current:
        chains.append(current)
    return chains


def compute_day_capacity(
    day: date,
    events: Sequence[FixedEvent],
    config: SchedulerConfig,
    deadline_cutoff: datetime | None = None,
) -> DayCapacity:
    # 1. Khung ngày
    window_cfg = config.day_windows.get(day.weekday())
    if window_cfg is None:
        return DayCapacity(
            date=day,
            free_intervals=(),
            busy_minutes=0,
            ceiling_minutes=0,
            capacity_minutes=0,
            dropped_fragments=(),
            leisure_interval=None,
        )

    tz = ZoneInfo(config.timezone)
    window = _interval_on_day(day, window_cfg.start, window_cfg.end, tz)
    free: tuple[Interval, ...] = (window,)

    # 2. Lọc event: bỏ all-day và non-opaque (không trừ khung, không tính busy)
    kept = [e for e in events if not e.is_all_day and e.is_opaque]

    # 3. Trừ event còn lại
    free = subtract(free, (Interval(e.start, e.end) for e in kept))

    # 4. Trừ break
    applicable_breaks = [
        b for b in config.breaks if not b.days or day.weekday() in b.days
    ]
    free = subtract(
        free,
        (_interval_on_day(day, b.start, b.end, tz) for b in applicable_breaks),
    )

    # 5 + 6. Đệm di chuyển + nghỉ hồi
    travel_events = sorted((e for e in kept if e.requires_travel), key=lambda e: e.start)
    chains = _build_chains(travel_events, config.travel.chain_gap_threshold)

    cuts: list[Interval] = []
    for chain in chains:
        first = chain[0]
        last = chain[-1]
        # Khoảng giữa các event trong chuỗi — trừ trọn
        for a, b in zip(chain, chain[1:]):
            if a.end < b.start:
                cuts.append(Interval(a.end, b.start))
        # pre ngay trước event đầu chuỗi
        if config.travel.pre_minutes > 0:
            cuts.append(
                Interval(
                    first.start - timedelta(minutes=config.travel.pre_minutes),
                    first.start,
                )
            )
        # post ngay sau event cuối chuỗi
        if config.travel.post_minutes > 0:
            cuts.append(
                Interval(
                    last.end,
                    last.end + timedelta(minutes=config.travel.post_minutes),
                )
            )

    # 6. Nghỉ hồi sau post của chuỗi cuối cùng trong ngày
    if chains and config.travel.recovery_minutes > 0:
        last_event = chains[-1][-1]
        post_end = last_event.end + timedelta(minutes=config.travel.post_minutes)
        cuts.append(
            Interval(
                post_end,
                post_end + timedelta(minutes=config.travel.recovery_minutes),
            )
        )

    free = subtract(free, cuts)

    # 7. Giải trí
    leisure_interval: Interval | None = None
    if config.leisure is not None and (
        not config.leisure.days or day.weekday() in config.leisure.days
    ):
        block = find_contiguous_block(
            free, config.leisure.minutes, config.leisure.placement
        )
        if block is None:
            block = longest_interval(free)
        if block is not None:
            leisure_interval = block
            free = subtract(free, (block,))

    # 8. Loại mảnh vụn
    free, dropped = drop_shorter_than(free, config.min_fragment_minutes)

    # 9. Cắt theo deadline
    if deadline_cutoff is not None:
        if deadline_cutoff <= window.start:
            free = ()
        else:
            free = clip(free, Interval(window.start, deadline_cutoff))

    # 10. busy_minutes: event đã giữ, normalize (hợp nhất chồng lấn), clip vào khung, total
    busy = normalize(Interval(e.start, e.end) for e in kept)
    busy = clip(busy, window)
    busy_minutes = total_minutes(busy)

    # 11. ceiling: busy >= threshold → ceiling_busy, ngược lại ceiling_free
    if busy_minutes >= config.ceiling.busy_threshold_minutes:
        ceiling = config.ceiling.ceiling_busy
    else:
        ceiling = config.ceiling.ceiling_free

    # 12. capacity
    capacity = min(total_minutes(free), ceiling)

    return DayCapacity(
        date=day,
        free_intervals=free,
        busy_minutes=busy_minutes,
        ceiling_minutes=ceiling,
        capacity_minutes=capacity,
        dropped_fragments=dropped,
        leisure_interval=leisure_interval,
    )
