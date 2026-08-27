"""Phân bổ phút làm việc cho assignment và ongoing task. Hàm thuần, không I/O."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Mapping, Sequence

from scheduler_core.config import SchedulerConfig
from scheduler_core.intervals import longest_interval
from scheduler_core.models import Assignment, DayCapacity, OngoingTask


# ---------------------------------------------------------------- result type


@dataclass(frozen=True)
class AllocationResult:
    """Kết quả phân bổ assignment. by_day delegate dict-like để test cũ không vỡ."""

    by_day: dict[date, dict[str, int]]
    shortfall: dict[str, int] = field(default_factory=dict)

    def __getitem__(self, key):
        return self.by_day[key]

    def __iter__(self):
        return iter(self.by_day)

    def __len__(self):
        return len(self.by_day)

    def __contains__(self, key):
        return key in self.by_day

    def values(self):
        return self.by_day.values()

    def items(self):
        return self.by_day.items()

    def get(self, key, default=None):
        return self.by_day.get(key, default)

    def keys(self):
        return self.by_day.keys()


# ---------------------------------------------------------------- largest remainder


def _largest_remainder(
    total: int,
    day_fc: list[tuple],
    unit: int,
    min_minutes: int,
) -> dict:
    """Phân `total' phút cho các ngày theo tỉ lệ free_capacity, largest remainder.

    Residual (< unit) dồn vào ngày đầu tiên (theo phần dư giảm dần) CÒN CHỖ,
    không phải luôn ngày có dư lớn nhất — tránh bị clip mất im lặng.
    """
    days = [d for d, _ in day_fc]
    if total <= 0 or not days:
        return {d: 0 for d in days}
    fc = {d: c for d, c in day_fc}
    total_fc = sum(fc.values())
    if total_fc <= 0:
        return {d: 0 for d in days}

    # Quá tải: total >= tổng fc → nhồi mỗi ngày đúng fc (nếu fc >= min).
    if total >= total_fc:
        return {d: (fc[d] if fc[d] >= min_minutes else 0) for d in days}

    total_units = total // unit
    residual = total % unit

    raw = []
    for d in days:
        raw_units = (total * fc[d]) / total_fc / unit
        floor_u = math.floor(raw_units)
        frac = raw_units - floor_u
        raw.append((d, floor_u, frac))
    sum_floor = sum(r[1] for r in raw)
    deficit = total_units - sum_floor

    order = sorted(raw, key=lambda r: (-r[2],))

    units = {d: fu for d, fu, _ in raw}
    for i in range(deficit):
        units[order[i][0]] += 1

    minutes = {d: units[d] * unit for d in days}
    if residual > 0:
        # Dồn residual vào ngày đầu tiên (theo dư giảm dần) còn chỗ.
        for d, _, _ in order:
            if minutes[d] + residual <= fc[d]:
                minutes[d] += residual
                break
        else:
            # Không ngày nào chứa nổi residual nguyên → rải nhỏ.
            left = residual
            for d, _, _ in order:
                room = fc[d] - minutes[d]
                if room > 0:
                    give = min(left, room)
                    minutes[d] += give
                    left -= give
                    if left <= 0:
                        break
    return minutes


def _place_min_blocks(total, days, fc, unit, min_minutes):
    """Rải `total' thành các khối >= min theo THỨ TỰ THỜI GIAN tăng dần.
    Phần dư < min gộp vào khối đầu; không xếp được khối nào → dồn vào ngày fc lớn nhất."""
    minutes = {d: 0 for d in days}
    if total <= 0:
        return minutes
    if total < min_minutes:
        order = sorted(days, key=lambda d: (-fc[d], d))
        minutes[order[0]] = total
        return minutes
    remaining = total
    placed: list = []
    for d in days:
        if remaining <= 0:
            break
        if fc[d] < min_minutes:
            continue
        if remaining < min_minutes:
            break
        minutes[d] = min_minutes
        remaining -= min_minutes
        placed.append(d)
    if remaining > 0:
        if placed:
            minutes[placed[0]] += remaining
        else:
            order = sorted(days, key=lambda d: (-fc[d], d))
            minutes[order[0]] = remaining
    return minutes


def _cleanup_min(minutes, days, fc, unit, min_minutes):
    """Dọn phân bổ 0 < x < min: về 0, gom lại rải cho ngày chưa có phân bổ còn chỗ.
    Ưu tiên zero-days → _place_min_blocks (thời gian) → spreading nhất quán.
    Không zero-day nào chứa nổi → thử survivor còn chỗ → fallback."""
    for _ in range(len(days) + 2):
        sub = [d for d in days if 0 < minutes[d] < min_minutes]
        if not sub:
            break
        collected = sum(minutes[d] for d in sub)
        for d in sub:
            minutes[d] = 0
        # Ưu tiên ngày chưa có phân bổ (minutes==0) còn chỗ → _place_min_blocks (thời gian).
        zero_days = [d for d in days if minutes[d] == 0 and fc[d] >= min_minutes]
        if zero_days:
            zero_fc = {d: fc[d] for d in zero_days}
            placed = _place_min_blocks(collected, zero_days, zero_fc, unit, min_minutes)
            for d in zero_days:
                minutes[d] += placed[d]
            break
        # Không zero-day nào chứa nổi → survivor còn chỗ.
        survivor_room = [(d, fc[d] - minutes[d]) for d in days if minutes[d] > 0 and fc[d] - minutes[d] > 0]
        if survivor_room:
            add = _largest_remainder(collected, survivor_room, unit, min_minutes)
            for d, _ in survivor_room:
                minutes[d] += add.get(d, 0)
        else:
            # Không ngày nào chứa nổi → fallback trên mọi ngày còn chỗ.
            remaining_fc = {d: fc[d] - minutes[d] for d in days}
            placed = _place_min_blocks(collected, days, remaining_fc, unit, min_minutes)
            for d in days:
                minutes[d] += placed[d]
            break
    return minutes


def _allocate_branch_a(remaining, day_fc, unit, min_minutes):
    days = [d for d, _ in day_fc]
    fc = {d: c for d, c in day_fc}
    minutes = _largest_remainder(remaining, day_fc, unit, min_minutes)
    minutes = _cleanup_min(minutes, days, fc, unit, min_minutes)

    # Clip + tái phân bổ phần bị cắt.
    for _ in range(len(days) + 1):
        for d in days:
            if minutes[d] > fc[d]:
                minutes[d] = fc[d]
        lost = remaining - sum(minutes.values())
        if lost <= 0:
            break
        room_days = [(d, fc[d] - minutes[d]) for d in days if fc[d] - minutes[d] > 0]
        if not room_days:
            break
        add = _largest_remainder(lost, room_days, unit, min_minutes)
        for d, _ in room_days:
            minutes[d] += add.get(d, 0)
        minutes = _cleanup_min(minutes, days, fc, unit, min_minutes)

    return minutes


def _allocate_branch_b(remaining, day_fc, min_minutes):
    """Nhánh B: ngày sớm nhất có free_capacity >= remaining → cấp trọn.
    Không có ngày nào đủ → chia thành các phần >= min theo thứ tự thời gian."""
    minutes = {d: 0 for d, _ in day_fc}
    if remaining <= 0:
        return minutes
    for d, cap in day_fc:
        if cap >= remaining:
            minutes[d] = remaining
            return minutes
    left = remaining
    for d, cap in day_fc:
        if left <= 0:
            break
        if cap < min_minutes:
            continue
        block = min(left, cap)
        if block < min_minutes:
            continue
        minutes[d] = block
        left -= block
    return minutes


# ---------------------------------------------------------------- public


def allocate_assignments(
    assignments: Sequence[Assignment],
    capacities: Mapping,
    config: SchedulerConfig,
) -> AllocationResult:
    """Phân bổ assignment theo độ khẩn (deadline tăng dần).

    Trả về AllocationResult: by_day = {date: {task_id: phút}}, shortfall = {task_id: phút thiếu}.
    """
    sorted_dates = sorted(capacities.keys())
    if not sorted_dates:
        return AllocationResult(by_day={}, shortfall={})
    first_day = sorted_dates[0]
    used = {d: 0 for d in sorted_dates}
    result = {d: {} for d in sorted_dates}
    shortfall: dict[str, int] = {}

    for task in sorted(assignments, key=lambda a: a.deadline):
        remaining = task.remaining_minutes
        if remaining <= 0:
            continue
        days_until = (task.deadline.date() - first_day).days
        task_days = [d for d in sorted_dates if d <= task.deadline.date()]
        day_fc = [(d, capacities[d].capacity_minutes - used[d]) for d in task_days]

        if remaining > config.small_task_threshold:
            branch = "A"
        elif days_until <= config.small_task_deadline_days:
            branch = "B"
        else:
            branch = "A"

        if branch == "A":
            alloc = _allocate_branch_a(
                remaining, day_fc, config.blocks.allocation_unit, config.blocks.min_minutes
            )
        else:
            alloc = _allocate_branch_b(remaining, day_fc, config.blocks.min_minutes)

        for d, m in alloc.items():
            if m > 0:
                result[d][task.task_id] = m
                used[d] += m

        allocated = sum(result[d].get(task.task_id, 0) for d in sorted_dates)
        gap = remaining - allocated
        if gap > 0:
            shortfall[task.task_id] = gap

    by_day = {d: alloc for d, alloc in result.items() if alloc}
    return AllocationResult(by_day=by_day, shortfall=shortfall)


def allocate_ongoing(
    ongoing: Sequence[OngoingTask],
    capacities: Mapping,
    assignment_alloc: Mapping,
    existing_blocks: Mapping,
    config: SchedulerConfig,
):
    """Phân bổ ongoing task vào phần dư sau assignment. Không sửa assignment_alloc.

    Trả về {date: {task_id: phút}}, chỉ chứa mục khác 0.
    """
    min_minutes = config.blocks.min_minutes
    sorted_dates = sorted(capacities.keys())
    result = {d: {} for d in sorted_dates}

    for day in sorted_dates:
        cap_day = capacities[day]
        assign_used = sum(assignment_alloc.get(day, {}).values())
        existing = existing_blocks.get(day, 0)
        leftover = cap_day.capacity_minutes - assign_used - existing

        longest = longest_interval(cap_day.free_intervals)
        has_room = longest is not None and longest.duration_minutes >= min_minutes
        if leftover < min_minutes or not has_room:
            continue

        for task in sorted(ongoing, key=lambda t: t.title):
            if leftover < min_minutes:
                break
            give = min(task.daily_target_minutes, leftover)
            if give < min_minutes:
                continue
            result[day][task.task_id] = give
            leftover -= give

    return {d: alloc for d, alloc in result.items() if alloc}
