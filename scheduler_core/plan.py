"""Tích hợp build_plan: gắn kết capacity → allocate → cut_blocks → run_checks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Mapping, Sequence

from scheduler_core.allocate import allocate_assignments, allocate_ongoing
from scheduler_core.blocks import cut_blocks
from scheduler_core.capacity import compute_day_capacity
from scheduler_core.config import SchedulerConfig
from scheduler_core.models import (
    Assignment,
    Block,
    CheckResult,
    DayCapacity,
    FixedEvent,
    OngoingTask,
)
from scheduler_core.validate import run_checks


@dataclass(frozen=True)
class PlanResult:
    blocks: list[Block]
    projected: dict[date, dict[str, int]]
    capacities: dict[date, DayCapacity]
    checks: list[CheckResult]
    ok: bool
    warnings: tuple[str, ...]
    completed_task_ids: tuple[str, ...]
    infeasible_task_ids: tuple[str, ...]
    shortfall: dict[str, int]


def _date_range(start: date, end: date) -> list[date]:
    days: list[date] = []
    d = start
    while d <= end:
        days.append(d)
        d += timedelta(days=1)
    return days


def build_plan(
    today: date,
    assignments: Sequence[Assignment],
    ongoing: Sequence[OngoingTask],
    events_by_day: Mapping[date, Sequence[FixedEvent]],
    existing_blocks: Mapping[date, int],
    config: SchedulerConfig,
) -> PlanResult:
    config.validate()

    d1 = today + timedelta(days=1)
    write_end = d1 + timedelta(days=config.write_horizon_days - 1)

    active = [a for a in assignments if a.remaining_minutes > 0]
    completed = tuple(
        sorted(a.task_id for a in assignments if a.remaining_minutes <= 0)
    )

    if active:
        max_deadline = max(a.deadline.date() for a in active)
        horizon_end = d1 + timedelta(days=config.planning_horizon_days - 1)
        dend = min(max_deadline, horizon_end)
    else:
        dend = write_end

    sorted_dates = _date_range(d1, dend)

    capacities: dict[date, DayCapacity] = {}
    for day in sorted_dates:
        events = events_by_day.get(day, [])
        capacities[day] = compute_day_capacity(day, events, config)

    infeasible: list[str] = []
    for a in active:
        task_days = [d for d in sorted_dates if d <= a.deadline.date()]
        capacity_task = sum(capacities[d].capacity_minutes for d in task_days)
        if a.remaining_minutes > capacity_task:
            infeasible.append(a.task_id)
    infeasible_t = tuple(sorted(infeasible))

    assignment_result = allocate_assignments(active, capacities, config)
    ongoing_alloc = allocate_ongoing(
        ongoing, capacities, assignment_result.by_day, existing_blocks, config
    )

    merged_alloc: dict[date, dict[str, int]] = {}
    for d in sorted_dates:
        merged: dict[str, int] = {}
        merged.update(assignment_result.by_day.get(d, {}))
        merged.update(ongoing_alloc.get(d, {}))
        merged_alloc[d] = merged

    tasks_map: dict[str, Assignment | OngoingTask] = {}
    for a in active:
        tasks_map[a.task_id] = a
    for o in ongoing:
        tasks_map[o.task_id] = o

    write_window = set(_date_range(d1, write_end))

    blocks_by_day: dict[date, list[Block]] = {}
    all_blocks: list[Block] = []
    for d in sorted(write_window):
        if d in capacities:
            day_blocks = cut_blocks(
                capacities[d], merged_alloc.get(d, {}), tasks_map, config
            )
            if day_blocks:
                blocks_by_day[d] = day_blocks
                all_blocks.extend(day_blocks)

    projected: dict[date, dict[str, int]] = {}
    for d in sorted_dates:
        if d not in write_window and merged_alloc.get(d):
            projected[d] = dict(merged_alloc[d])

    checks = run_checks(blocks_by_day, capacities, active, ongoing, merged_alloc, config,
                        events_by_day=events_by_day)
    ok = all(c.passed for c in checks if c.severity == "error")
    warnings = tuple(c.name for c in checks if c.severity == "warning" and not c.passed)

    return PlanResult(
        blocks=all_blocks,
        projected=projected,
        capacities=capacities,
        checks=checks,
        ok=ok,
        warnings=warnings,
        completed_task_ids=completed,
        infeasible_task_ids=infeasible_t,
        shortfall=assignment_result.shortfall,
    )
