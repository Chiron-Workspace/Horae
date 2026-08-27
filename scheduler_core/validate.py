"""Kiểm tra tính hợp lệ của lịch đã xếp. Trả về list[CheckResult], không raise.

Quan trọng: no_overlap_with_blocked dùng forbidden_intervals (đường tính độc lập
từ verify.py), KHÔNG dùng free_intervals của capacity — phá vỡ tautology.
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Mapping, Sequence
from zoneinfo import ZoneInfo

from scheduler_core.config import SchedulerConfig
from scheduler_core.intervals import normalize, subtract
from scheduler_core.models import Assignment, Block, CheckResult, DayCapacity, Interval, OngoingTask
from scheduler_core.verify import forbidden_intervals, expected_free_intervals, compute_leisure


def _intervals_repr(intervals: tuple[Interval, ...]) -> str:
    """In tóm tắt tập khoảng để so bằng mắt."""
    if not intervals:
        return "()"
    return ", ".join(f"{iv.start:%H:%M}–{iv.end:%H:%M}({iv.duration_minutes}')" for iv in intervals)


def _iv_str(iv: Interval | None) -> str:
    """In một Interval (hoặc None)."""
    if iv is None:
        return "None"
    return f"{iv.start:%H:%M}–{iv.end:%H:%M}({iv.duration_minutes}')"


def run_checks(
    blocks: Mapping,
    capacities: Mapping,
    assignments: Sequence[Assignment],
    ongoing: Sequence[OngoingTask],
    allocation: Mapping,
    config: SchedulerConfig,
    events_by_day: Mapping[date, Sequence],
) -> list[CheckResult]:
    """Chạy 12 mục kiểm. Trả về list[CheckResult], không bao giờ raise."""
    results: list[CheckResult] = []
    tz = ZoneInfo(config.timezone)
    min_minutes = config.blocks.min_minutes
    max_minutes = config.blocks.max_minutes
    sorted_dates = sorted(capacities.keys())
    first_day = sorted_dates[0] if sorted_dates else None

    assignment_map = {a.task_id: a for a in assignments}

    # Gom block theo ngày.
    blocks_by_day: dict = {day: list(bl) for day, bl in blocks.items()}

    all_blocks: list[Block] = []
    for day in sorted(blocks_by_day.keys()):
        all_blocks.extend(blocks_by_day[day])

    # 1. within_day_window
    bad = []
    for day, day_blocks in blocks_by_day.items():
        window_cfg = config.day_windows.get(day.weekday())
        if window_cfg is None:
            for b in day_blocks:
                bad.append(f"{b.title} ngày {day}: không có day_window cho thứ {day.weekday()}")
            continue
        ws = datetime.combine(day, window_cfg.start, tzinfo=tz)
        we = datetime.combine(day, window_cfg.end, tzinfo=tz)
        for b in day_blocks:
            if b.start < ws or b.end > we:
                bad.append(f"{b.title} ngày {day}: {b.start:%H:%M}–{b.end:%H:%M} ngoài window {window_cfg.start:%H:%M}–{window_cfg.end:%H:%M}")
    results.append(CheckResult("within_day_window", not bad, "; ".join(bad) if bad else "OK"))

    # 2. no_overlap_with_blocked — dùng forbidden_intervals (luôn tự tính leisure, độc lập)
    bad = []
    for day, day_blocks in blocks_by_day.items():
        cap = capacities.get(day)
        if cap is None:
            continue
        events = events_by_day.get(day, [])
        forbidden = forbidden_intervals(day, events, config)
        for b in day_blocks:
            overlaps = any(
                not (b.end <= f.start or b.start >= f.end)
                for f in forbidden
            )
            if overlaps:
                bad.append(f"{b.title} ngày {day}: {b.start:%H:%M}–{b.end:%H:%M} giao vùng cấm")
    results.append(CheckResult("no_overlap_with_blocked", not bad, "; ".join(bad) if bad else "OK"))

    # 3. no_block_overlap
    bad = []
    sorted_blocks = sorted(all_blocks, key=lambda b: b.start)
    for a, b in zip(sorted_blocks, sorted_blocks[1:]):
        if b.start < a.end:
            bad.append(f"{a.title} {a.start:%H:%M}–{a.end:%H:%M} chồng {b.title} {b.start:%H:%M}–{b.end:%H:%M}")
    results.append(CheckResult("no_block_overlap", not bad, "; ".join(bad) if bad else "OK"))

    # 4. within_ceiling
    bad = []
    for day, day_blocks in blocks_by_day.items():
        cap = capacities.get(day)
        if cap is None:
            continue
        total = sum(b.duration_minutes for b in day_blocks)
        if total > cap.ceiling_minutes:
            bad.append(f"ngày {day}: {total}' > ceiling {cap.ceiling_minutes}'")
    results.append(CheckResult("within_ceiling", not bad, "; ".join(bad) if bad else "OK"))

    # 5. within_remaining
    bad = []
    for a in assignments:
        alloc_total = sum(
            allocation.get(day, {}).get(a.task_id, 0) for day in sorted_dates
        )
        if alloc_total > a.remaining_minutes:
            bad.append(f"{a.task_id}: phân bổ {alloc_total}' > remaining {a.remaining_minutes}'")
    results.append(CheckResult("within_remaining", not bad, "; ".join(bad) if bad else "OK"))

    # 6. before_deadline
    bad = []
    for b in all_blocks:
        if b.kind != "assignment":
            continue
        a = assignment_map.get(b.task_id)
        if a is not None and b.end >= a.deadline:
            bad.append(f"{b.task_id}: block kết thúc {b.end:%H:%M} >= deadline {a.deadline:%H:%M}")
    results.append(CheckResult("before_deadline", not bad, "; ".join(bad) if bad else "OK"))

    # 7. block_size
    bad = []
    for b in all_blocks:
        d = b.duration_minutes
        if d < min_minutes or d > max_minutes:
            bad.append(f"{b.title}: {d}' ngoài [{min_minutes}', {max_minutes}']")
    results.append(CheckResult("block_size", not bad, "; ".join(bad) if bad else "OK"))

    # 8. no_day_dominance (warning)
    bad = []
    for a in assignments:
        if a.remaining_minutes <= config.small_task_threshold:
            continue
        for day in sorted_dates:
            day_alloc = allocation.get(day, {}).get(a.task_id, 0)
            if day_alloc > a.remaining_minutes // 2:
                bad.append(f"{a.task_id} ngày {day}: {day_alloc}' > 50% của remaining {a.remaining_minutes}'")
    results.append(CheckResult("no_day_dominance", not bad, "; ".join(bad) if bad else "OK", severity="warning"))

    # 9. small_task_buffer (warning)
    bad = []
    if first_day is not None:
        for a in assignments:
            remaining = a.remaining_minutes
            days_until = (a.deadline.date() - first_day).days
            is_branch_b = (
                remaining <= config.small_task_threshold
                and days_until <= config.small_task_deadline_days
            )
            if not is_branch_b:
                continue
            for b in all_blocks:
                if b.task_id == a.task_id and b.start.date() >= a.deadline.date():
                    bad.append(f"{a.task_id}: block ngày {b.start:%Y-%m-%d} không trước deadline {a.deadline:%Y-%m-%d} ít nhất 1 ngày")
    results.append(CheckResult("small_task_buffer", not bad, "; ".join(bad) if bad else "OK", severity="warning"))

    # 10. no_tiny_fragments
    bad = []
    for day in sorted_dates:
        cap = capacities[day]
        for iv in cap.free_intervals:
            if iv.duration_minutes < config.min_fragment_minutes:
                bad.append(f"ngày {day}: mảnh {iv.start:%H:%M}–{iv.end:%H:%M} ({iv.duration_minutes}') < min_fragment {config.min_fragment_minutes}'")
    results.append(CheckResult("no_tiny_fragments", not bad, "; ".join(bad) if bad else "OK"))

    # 11. capacity_consistent — hai đường tính toán độc lập phải khớp free_intervals
    bad = []
    for day in sorted_dates:
        cap = capacities[day]
        events = events_by_day.get(day, [])
        expected = expected_free_intervals(day, events, config)
        actual = cap.free_intervals
        if expected != actual:
            bad.append(
                f"ngày {day}: free_intervals lệch — "
                f"verify={_intervals_repr(expected)} vs capacity={_intervals_repr(actual)}"
            )
    results.append(CheckResult("capacity_consistent", not bad, "; ".join(bad) if bad else "OK"))

    # 12. leisure_matches — leisure do capacity tính phải khớp leisure do verify tự tính
    bad = []
    for day in sorted_dates:
        cap = capacities[day]
        events = events_by_day.get(day, [])
        computed = compute_leisure(day, events, config)
        actual_leisure = cap.leisure_interval
        if computed != actual_leisure:
            bad.append(
                f"ngày {day}: leisure lệch — "
                f"verify={_iv_str(computed)} vs capacity={_iv_str(actual_leisure)}"
            )
    results.append(CheckResult("leisure_matches", not bad, "; ".join(bad) if bad else "OK"))

    return results
