"""Cắt phân bổ thành các Block và đặt earliest-fit vào free_intervals."""

from __future__ import annotations

import math
from datetime import timedelta
from typing import Mapping, Union

from scheduler_core.config import SchedulerConfig
from scheduler_core.models import Assignment, Block, DayCapacity, Interval, OngoingTask


def _split_part(part: int, min_minutes: int, max_minutes: int, allocation_unit: int | None = None) -> list[int]:
    """Chia `part' phút thành block. Ưu tiên:
    1. Mọi block là bội của allocation_unit
    2. n nhỏ nhất
    3. Đều nhau nhất
    Không có cách chia toàn bội unit → bỏ điều kiện 1, lấy n nhỏ nhất + đều nhau.
    """
    if part <= 0:
        return []
    if part <= max_minutes:
        return [part]

    # Thử chia với ràng buộc unit
    if allocation_unit is not None and allocation_unit > 0 and part % allocation_unit == 0:
        total_units = part // allocation_unit
        min_units = min_minutes // allocation_unit
        max_units = max_minutes // allocation_unit
        if min_units > 0 and max_units > 0:
            n_min = math.ceil(total_units / max_units)
            n_max = total_units // min_units
            if n_min <= n_max:
                n = n_min
                base_u = total_units // n
                rem_u = total_units % n
                sizes_u = [base_u + (1 if i < rem_u else 0) for i in range(n)]
                if all(min_units <= u <= max_units for u in sizes_u):
                    return [u * allocation_unit for u in sizes_u]

    # Fallback: n nhỏ nhất, đều nhau nhất (không ràng buộc unit)
    n = math.ceil(part / max_minutes)
    base = part // n
    rem = part % n
    return [base + (1 if i < rem else 0) for i in range(n)]


def _minutes_in_day(dt) -> int:
    return dt.hour * 60 + dt.minute


def _round_start_up(start, unit: int):
    """Đẩy `start` lên bội số gần nhất của `unit' phút trong ngày (lên trên). Đã là bội số → giữ."""
    m = _minutes_in_day(start)
    rem = m % unit
    if rem == 0:
        return start
    return start + timedelta(minutes=(unit - rem))


def cut_blocks(
    day_capacity: DayCapacity,
    allocation: Mapping[str, int],
    tasks: Mapping[str, Union[Assignment, OngoingTask]],
    config: SchedulerConfig,
) -> list[Block]:
    """Cắt `allocation` của một ngày thành Block, đặt earliest-fit vào free_intervals."""
    free = list(day_capacity.free_intervals)
    min_minutes = config.blocks.min_minutes
    max_minutes = config.blocks.max_minutes
    round_unit = config.blocks.round_start_to

    # 1. Tạo danh sách block thô, tách assignment / ongoing để sort riêng.
    assignment_items: list[tuple] = []  # (deadline, title, task_id, size)
    ongoing_items: list[tuple] = []  # (None, title, task_id, size)
    for task_id, part in allocation.items():
        if part <= 0:
            continue
        task = tasks[task_id]
        sizes = _split_part(part, min_minutes, max_minutes, config.blocks.allocation_unit)
        if isinstance(task, Assignment):
            for size in sizes:
                assignment_items.append((task.deadline, task.title, task_id, size))
        else:
            for size in sizes:
                ongoing_items.append((None, task.title, task_id, size))

    assignment_items.sort(key=lambda x: (x[0], x[1], x[2]))
    ongoing_items.sort(key=lambda x: (x[1], x[2]))
    ordered = assignment_items + ongoing_items

    # 2. Đặt earliest-fit.
    blocks: list[Block] = []
    cursor = None  # end của block đặt gần nhất
    last_task_id: str | None = None

    for deadline, title, task_id, size in ordered:
        kind = "assignment" if deadline is not None else "ongoing"
        # Gap so với block đặt ngay trước.
        if last_task_id is None:
            gap = 0
        elif task_id == last_task_id:
            gap = config.blocks.same_task_gap
        else:
            gap = config.blocks.switch_task_gap
        earliest = cursor + timedelta(minutes=gap) if cursor is not None else None

        placed = False
        for frag in free:
            avail_start = frag.start if earliest is None else max(frag.start, earliest)
            if avail_start + timedelta(minutes=size) > frag.end:
                continue
            # Thử làm tròn trước, rồi giờ lẻ.
            candidates = [avail_start]
            if round_unit is not None:
                rounded = _round_start_up(avail_start, round_unit)
                if rounded != avail_start and rounded + timedelta(minutes=size) <= frag.end:
                    candidates = [rounded, avail_start]
            for start in candidates:
                end = start + timedelta(minutes=size)
                if end > frag.end:
                    continue
                if kind == "assignment" and deadline is not None and end >= deadline:
                    continue
                blocks.append(
                    Block(task_id=task_id, title=title, start=start, end=end, kind=kind)  # type: ignore[arg-type]
                )
                cursor = end
                last_task_id = task_id
                placed = True
                break
            if placed:
                break
        # Không đặt được → bỏ phần còn lại (ghi NOTES nếu xảy ra trong test).

    return blocks
