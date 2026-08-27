"""Đường tính toán độc lập cho run_checks. KHÔNG import capacity.py.

Viết theo hướng CỘNG DỖN các vùng cấm từ dữ liệu thô, ngược với capacity.py
(trừ dần từ khung ngày). Hai bên dùng chung intervals.py/models.py/config.py
nhưng logic độc lập — nếu cùng bug thì lưới vẫn vô dụng.

Leisure: LUÔN tự tính từ config, không nhận từ ngoài. Lệch leisure
bị bắt trực tiếp bởi check leisure_matches.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Sequence
from zoneinfo import ZoneInfo

from scheduler_core.config import SchedulerConfig
from scheduler_core.intervals import normalize, subtract, clip, drop_shorter_than, find_contiguous_block, longest_interval, total_minutes
from scheduler_core.models import FixedEvent, Interval


def _interval_on_day(day: date, start: time, end: time, tz: ZoneInfo) -> Interval:
    """Tạo Interval từ time trong ngày."""
    return Interval(
        datetime.combine(day, start, tzinfo=tz),
        datetime.combine(day, end, tzinfo=tz),
    )


def _build_chains(
    events: list[FixedEvent], threshold_minutes: int
) -> list[list[FixedEvent]]:
    """Gom event travel thành chuỗi. Gap < threshold → cùng chuỗi. Bắc cầu."""
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


def _forbidden_before_leisure(
    day: date,
    events: Sequence[FixedEvent],
    config: SchedulerConfig,
) -> tuple[Interval, ...]:
    """Cộng dồn event + break + travel padding (chưa có leisure). Trả về tuple đã normalize."""
    forbidden: list[Interval] = []

    window_cfg = config.day_windows.get(day.weekday())
    if window_cfg is None:
        return ()
    tz = ZoneInfo(config.timezone)

    kept = [e for e in events if not e.is_all_day and e.is_opaque]
    for e in kept:
        forbidden.append(Interval(e.start, e.end))

    for b in config.breaks:
        if not b.days or day.weekday() in b.days:
            forbidden.append(_interval_on_day(day, b.start, b.end, tz))

    travel_events = sorted((e for e in kept if e.requires_travel), key=lambda e: e.start)
    chains = _build_chains(travel_events, config.travel.chain_gap_threshold)

    for chain in chains:
        first = chain[0]
        last = chain[-1]
        for a, b in zip(chain, chain[1:]):
            if a.end < b.start:
                forbidden.append(Interval(a.end, b.start))
        if config.travel.pre_minutes > 0:
            forbidden.append(Interval(
                first.start - timedelta(minutes=config.travel.pre_minutes),
                first.start,
            ))
        if config.travel.post_minutes > 0:
            forbidden.append(Interval(
                last.end,
                last.end + timedelta(minutes=config.travel.post_minutes),
            ))

    if chains and config.travel.recovery_minutes > 0:
        last_event = chains[-1][-1]
        post_end = last_event.end + timedelta(minutes=config.travel.post_minutes)
        forbidden.append(Interval(
            post_end,
            post_end + timedelta(minutes=config.travel.recovery_minutes),
        ))

    return normalize(forbidden)


def compute_leisure(
    day: date,
    events: Sequence[FixedEvent],
    config: SchedulerConfig,
) -> Interval | None:
    """Tự tính vị trí leisure từ config và vùng cấm (trước leisure).

    Độc lập với capacity.py: dùng find_contiguous_block + longest_interval
    trên free = window trừ forbidden_before_leisure.
    """
    window_cfg = config.day_windows.get(day.weekday())
    if window_cfg is None:
        return None
    if config.leisure is None:
        return None
    if not (not config.leisure.days or day.weekday() in config.leisure.days):
        return None
    tz = ZoneInfo(config.timezone)
    forbidden_before = _forbidden_before_leisure(day, events, config)
    window = _interval_on_day(day, window_cfg.start, window_cfg.end, tz)
    free_before_leisure = subtract((window,), forbidden_before)
    block = find_contiguous_block(
        free_before_leisure, config.leisure.minutes, config.leisure.placement
    )
    if block is None:
        block = longest_interval(free_before_leisure)
    return block


def forbidden_intervals(
    day: date,
    events: Sequence[FixedEvent],
    config: SchedulerConfig,
) -> tuple[Interval, ...]:
    """Dựng TOÀN BỘ vùng cấm: event + break + travel + leisure TỰ TÍNH.

    Không nhận leisure từ ngoài. Luôn tự tính để giữ độc lập hoàn toàn.
    """
    forbidden = list(_forbidden_before_leisure(day, events, config))
    computed_leisure = compute_leisure(day, events, config)
    if computed_leisure is not None:
        forbidden.append(computed_leisure)
    return normalize(forbidden)


def expected_free_intervals(
    day: date,
    events: Sequence[FixedEvent],
    config: SchedulerConfig,
) -> tuple[Interval, ...]:
    """Tính free_intervals kỳ vọng: day_window trừ forbidden (tự tính leisure), rồi bỏ mảnh < min_fragment."""
    window_cfg = config.day_windows.get(day.weekday())
    if window_cfg is None:
        return ()
    tz = ZoneInfo(config.timezone)
    window = _interval_on_day(day, window_cfg.start, window_cfg.end, tz)

    forbidden = forbidden_intervals(day, events, config)

    free = subtract((window,), forbidden)
    free, _ = drop_shorter_than(free, config.min_fragment_minutes)

    return free
