"""Diff reconciliation for the writable Auto-Study calendar.

The scheduler produces a desired set of blocks, while the calendar is the
source of truth for what was actually written.  This module deliberately
keeps those two concerns separate: first it computes a diff, then it applies
that diff only when the corresponding delete/create safety gate passes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

from horae.adapters.protocols import AutoBlock, BlockMeta, CalendarWriter
from horae.settings import AUTO_BLOCK_PREFIX, AUTO_STUDY_CALENDAR, TODOIST_ID_PREFIX
from scheduler_core.models import Block
from scheduler_core.plan import PlanResult


_TASK_ID_RE = re.compile(rf"{re.escape(TODOIST_ID_PREFIX)}\s*(\S+)")


@dataclass(frozen=True)
class DeleteOutcome:
    """Result of one explicit event deletion attempt."""

    event_id: str
    success: bool
    error: str | None = None


@dataclass(frozen=True)
class CreateOutcome:
    """Result of one block creation attempt."""

    block: Block
    event_id: str | None
    error: str | None = None


@dataclass(frozen=True)
class ReconciliationReport:
    """Diff, safety-gate result, side effects, and read-back diagnostics."""

    planned_deletes: tuple[AutoBlock, ...]
    planned_creates: tuple[Block, ...]
    deleted: tuple[DeleteOutcome, ...]
    created: tuple[CreateOutcome, ...]
    skipped_existing: tuple[Block, ...]
    recovered: tuple[Block, ...]
    warnings: tuple[str, ...]
    errors: tuple[str, ...]
    mismatch: tuple[str, ...]
    gate_passed: bool
    gate_delete: bool
    gate_create: bool
    total_auto_blocks: int
    deletion_limit: float
    target_calendar_id: str | None = None

    @property
    def planned_delete_ids(self) -> tuple[str, ...]:
        return tuple(block.event_id for block in self.planned_deletes)

    @property
    def planned_create_blocks(self) -> tuple[Block, ...]:
        return self.planned_creates

    @property
    def to_delete(self) -> tuple[AutoBlock, ...]:
        return self.planned_deletes

    @property
    def to_create(self) -> tuple[Block, ...]:
        return self.planned_creates

    @property
    def delete_candidates(self) -> tuple[AutoBlock, ...]:
        return self.planned_deletes

    @property
    def create_candidates(self) -> tuple[Block, ...]:
        return self.planned_creates

    @property
    def deleted_ids(self) -> tuple[str, ...]:
        return tuple(result.event_id for result in self.deleted if result.success)

    @property
    def created_blocks(self) -> tuple[CreateOutcome, ...]:
        return self.created

    @property
    def actual_deletes(self) -> tuple[DeleteOutcome, ...]:
        return self.deleted

    @property
    def actual_deleted(self) -> tuple[DeleteOutcome, ...]:
        return self.deleted

    @property
    def actual_creates(self) -> tuple[CreateOutcome, ...]:
        return self.created

    @property
    def actual_created(self) -> tuple[CreateOutcome, ...]:
        return self.created

    @property
    def safety_gate_passed(self) -> bool:
        return self.gate_passed

    @property
    def delete_gate_passed(self) -> bool:
        return self.gate_delete

    @property
    def create_gate_passed(self) -> bool:
        return self.gate_create

    @property
    def gate_delete_passed(self) -> bool:
        return self.gate_delete

    @property
    def gate_create_passed(self) -> bool:
        return self.gate_create

    @property
    def max_deletions(self) -> float:
        return self.deletion_limit

    @property
    def has_mismatch(self) -> bool:
        return bool(self.mismatch)


# Short aliases keep the public API easy to discover without duplicating the
# report implementation.
ReconcileReport = ReconciliationReport
ReconciliationResult = ReconciliationReport
DeleteResult = DeleteOutcome
CreateResult = CreateOutcome


def _duration(block: AutoBlock | Block) -> int:
    return max(0, int((block.end - block.start).total_seconds() // 60))


def _in_window(block: AutoBlock | Block, d1: date, d2: date) -> bool:
    day = block.start.date()
    return d1 <= day <= d2


def _task_ids(value: Iterable[str] | Mapping[str, Any] | None) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        return {value}
    if isinstance(value, Mapping):
        return {str(task_id) for task_id in value.keys()}
    return {
        str(getattr(task, "task_id", task))
        for task in value
    }


def _task_id_from_description(description: str) -> str | None:
    if not description:
        return None
    match = _TASK_ID_RE.search(description)
    return match.group(1) if match else None


def _sort_latest(blocks: Sequence[AutoBlock]) -> list[AutoBlock]:
    return sorted(blocks, key=lambda b: (b.start, b.end, b.event_id), reverse=True)


def _prefer_subset(candidate: tuple[int, ...], current: tuple[int, ...]) -> bool:
    """Prefer the subset containing later-sorted items.

    The input to ``_exact_subset`` is latest-first for deletions and deficit
    creation.  Lexicographically smaller indices therefore preserve as many
    of the latest blocks as possible, which in turn retains earlier blocks.
    """

    return candidate < current


def _exact_subset(
    blocks: Sequence[AutoBlock | Block],
    target_minutes: int,
) -> tuple[AutoBlock | Block, ...] | None:
    """Find a deterministic subset whose duration is exactly target_minutes."""
    if target_minutes < 0:
        return None
    if target_minutes == 0:
        return ()

    states: dict[int, tuple[int, ...]] = {0: ()}
    for index, block in enumerate(blocks):
        minutes = _duration(block)
        if minutes <= 0:
            continue
        for total, selected in list(states.items()):
            new_total = total + minutes
            if new_total > target_minutes:
                continue
            new_selected = selected + (index,)
            previous = states.get(new_total)
            if previous is None or _prefer_subset(new_selected, previous):
                states[new_total] = new_selected

    selected = states.get(target_minutes)
    if selected is None:
        return None
    return tuple(blocks[index] for index in selected)


def _match_planned_blocks(
    planned: Sequence[Block],
    existing: Sequence[AutoBlock],
) -> tuple[tuple[Block, ...], tuple[Block, ...], tuple[AutoBlock, ...]]:
    """Match existing blocks to planned blocks by exact interval.

    Totals drive the diff, but exact matches prevent a partial-create retry from
    creating a duplicate of a block that was already written successfully.
    """
    remaining = list(existing)
    matched: list[Block] = []
    matched_existing: list[AutoBlock] = []
    unmatched: list[Block] = []
    for planned_block in sorted(
        planned, key=lambda b: (b.start, b.end, b.task_id, b.title)
    ):
        match_index = next(
            (
                index
                for index, existing_block in enumerate(remaining)
                if existing_block.start == planned_block.start
                and existing_block.end == planned_block.end
            ),
            None,
        )
        if match_index is None:
            unmatched.append(planned_block)
        else:
            matched.append(planned_block)
            matched_existing.append(remaining.pop(match_index))
    return tuple(matched), tuple(unmatched), tuple(matched_existing)


def _matching_planned_blocks(
    planned: Sequence[Block],
    existing: Sequence[AutoBlock],
) -> tuple[tuple[Block, ...], tuple[Block, ...]]:
    matched, unmatched, _matched_existing = _match_planned_blocks(planned, existing)
    return matched, unmatched


def _latest_cover(
    blocks: Sequence[AutoBlock],
    target_minutes: int,
) -> tuple[AutoBlock, ...]:
    """Choose latest whole blocks whose duration covers target_minutes."""
    if target_minutes <= 0:
        return ()
    selected: list[AutoBlock] = []
    covered = 0
    for block in blocks:
        minutes = _duration(block)
        if minutes <= 0:
            continue
        selected.append(block)
        covered += minutes
        if covered >= target_minutes:
            return tuple(selected)
    return ()


def _deficit_blocks(
    blocks: Sequence[Block],
    deficit: int,
    existing: Sequence[AutoBlock] = (),
) -> tuple[Block, ...]:
    """Choose planned blocks totaling exactly deficit minutes.

    A normal plan has block-sized deficits, so this returns existing plan
    objects.  If a prior partial write leaves a non-block-sized deficit, a
    final planned block is shortened rather than over-writing extra minutes.
    """
    if deficit <= 0:
        return ()
    _matched, unmatched = _matching_planned_blocks(blocks, existing)
    latest_first = sorted(
        unmatched,
        key=lambda b: (b.start, b.end, b.task_id, b.title),
        reverse=True,
    )
    exact = _exact_subset(latest_first, deficit)
    if exact is not None:
        return tuple(sorted(exact, key=lambda b: (b.start, b.end, b.task_id, b.title)))

    remaining = deficit
    selected: list[Block] = []
    for block in latest_first:
        minutes = _duration(block)
        if minutes <= 0:
            continue
        take = min(minutes, remaining)
        if take == minutes:
            selected.append(block)
        else:
            # Keep the planned end and use the latest part of this block.
            selected.append(
                replace(block, start=block.end - timedelta(minutes=take))
            )
        remaining -= take
        if remaining == 0:
            break
    return tuple(sorted(selected, key=lambda b: (b.start, b.end, b.task_id, b.title)))


def _planned_blocks(plan: PlanResult | None, blocks: Sequence[Block] | None) -> list[Block]:
    if blocks is not None:
        return list(blocks)
    if plan is None:
        return []
    return list(plan.blocks)


def _read_writer_value(writer: object, names: Sequence[str]) -> tuple[Any, str | None]:
    """Read an optional writer target attribute or zero-argument method."""
    for name in names:
        if not hasattr(writer, name):
            continue
        value = getattr(writer, name)
        try:
            value = value() if callable(value) else value
        except Exception as exc:  # target resolution is part of the gate
            return None, f"writer target {name} lỗi: {exc}"
        return value, None
    return None, None


def _resolve_target(
    writer: CalendarWriter | None,
    requested_name: str,
    requested_id: str | None,
    delete_candidates: Sequence[AutoBlock],
) -> tuple[str | None, str | None, list[str]]:
    errors: list[str] = []
    writer_name: str | None = None
    writer_id: str | None = None

    if writer is not None:
        value, error = _read_writer_value(
            writer,
            ("target_calendar_name", "calendar_name"),
        )
        if error:
            errors.append(error)
        elif isinstance(value, str):
            writer_name = value

        value, error = _read_writer_value(
            writer,
            (
                "resolve_calendar_id",
                "get_target_calendar_id",
                "target_calendar_id",
                "calendar_id",
            ),
        )
        if error:
            errors.append(error)
        elif isinstance(value, str) and value:
            writer_id = value

    if writer_name is None and writer is not None:
        errors.append(
            f"writer không xác nhận được target calendar '{requested_name}'"
        )
    elif writer_name != requested_name:
        errors.append(
            f"writer target calendar '{writer_name}' không phải '{requested_name}'"
        )

    target_id = requested_id or writer_id
    if requested_id and writer_id and requested_id != writer_id:
        errors.append(
            f"calendar_id yêu cầu '{requested_id}' khác target writer '{writer_id}'"
        )

    return target_id, writer_name, errors


def _readback_range(
    d1: date,
    d2: date,
    existing: Sequence[AutoBlock],
    planned: Sequence[Block],
    start: datetime | None,
    end: datetime | None,
) -> tuple[datetime, datetime]:
    if start is not None and end is not None:
        return start, end
    datetimes = [block.start for block in existing] + [block.start for block in planned]
    tz = datetimes[0].tzinfo if datetimes and datetimes[0].tzinfo is not None else ZoneInfo("UTC")
    return (
        datetime.combine(d1, time.min, tzinfo=tz),
        datetime.combine(d2 + timedelta(days=1), time.min, tzinfo=tz),
    )


def _totals(blocks: Sequence[AutoBlock], d1: date, d2: date) -> dict[tuple[str, date], int]:
    result: dict[tuple[str, date], int] = {}
    for block in blocks:
        if not _in_window(block, d1, d2):
            continue
        key = (block.task_id, block.start.date())
        result[key] = result.get(key, 0) + _duration(block)
    return result


def reconcile(
    plan: PlanResult | None = None,
    existing_blocks: Sequence[AutoBlock] | None = None,
    task_ids: Iterable[str] | Mapping[str, Any] | None = None,
    d1: date | None = None,
    d2: date | None = None,
    writer: CalendarWriter | None = None,
    dry_run: bool = True,
    *,
    target_calendar_name: str = AUTO_STUDY_CALENDAR,
    target_calendar_id: str | None = None,
    readback_start: datetime | None = None,
    readback_end: datetime | None = None,
    blocks: Sequence[Block] | None = None,
    existing: Sequence[AutoBlock] | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    calendar_id: str | None = None,
    calendar_name: str | None = None,
    planned_state: Mapping[tuple[str, date], int] | None = None,
    now: datetime | None = None,
) -> ReconciliationReport:
    """Reconcile planned blocks against existing AutoBlocks.

    ``d1`` and ``d2`` are inclusive.  Blocks before ``d1`` are ignored for
    both the diff and deletion safety checks, so a past block can never be
    deleted by this function.  ``writer`` is only used after the relevant
    safety gate;
    with ``dry_run=True`` the returned plan is exact but no writer method is
    called.  ``now`` is an optional caller-supplied reference time used to
    protect replacement deletes that have already started; omitting it keeps
    the historical behavior and performs no clock lookup.
    """
    if existing_blocks is None:
        existing_blocks = existing if existing is not None else ()
    window_supplied = d1 is not None and d2 is not None
    if d1 is None:
        d1 = start_date
    if d2 is None:
        d2 = end_date
    window_supplied = window_supplied or (d1 is not None and d2 is not None)
    if isinstance(d1, datetime):
        d1 = d1.date()
    if isinstance(d2, datetime):
        d2 = d2.date()

    all_existing = list(existing_blocks)
    all_planned = _planned_blocks(plan, blocks)
    dates = [block.start.date() for block in all_existing + all_planned]
    if d1 is None:
        d1 = min(dates) if dates else date.min
    if d2 is None:
        d2 = max(dates) if dates else d1
    if d2 < d1:
        d2 = d1

    requested_id = target_calendar_id or calendar_id
    requested_name = calendar_name if calendar_name is not None else target_calendar_name
    current_existing = [block for block in all_existing if _in_window(block, d1, d2)]
    current_planned = [block for block in all_planned if _in_window(block, d1, d2)]
    current_task_ids = _task_ids(task_ids)
    if task_ids is None:
        current_task_ids.update(block.task_id for block in current_planned)

    planned_totals: dict[tuple[str, date], int] = {}
    for block in current_planned:
        key = (block.task_id, block.start.date())
        planned_totals[key] = planned_totals.get(key, 0) + _duration(block)
    if planned_state is not None:
        planned_totals = {
            (task_id, day): max(0, int(minutes))
            for (task_id, day), minutes in planned_state.items()
            if d1 <= day <= d2
        }

    existing_totals = _totals(current_existing, d1, d2)
    keys = sorted(set(existing_totals) | set(planned_totals))
    existing_by_key: dict[tuple[str, date], list[AutoBlock]] = {}
    planned_by_key: dict[tuple[str, date], list[Block]] = {}
    for block in current_existing:
        existing_by_key.setdefault((block.task_id, block.start.date()), []).append(block)
    for block in current_planned:
        planned_by_key.setdefault((block.task_id, block.start.date()), []).append(block)

    planned_deletes: list[AutoBlock] = []
    planned_creates: list[Block] = []
    skipped_existing: list[Block] = []
    recovered: list[Block] = []
    diff_warnings: list[str] = []
    diff_delete_errors: list[str] = []
    diff_create_errors: list[str] = []
    replacement_requirements: dict[int, tuple[str, ...]] = {}
    replacement_delete_candidates: set[int] = set()

    for key in keys:
        existing_total = existing_totals.get(key, 0)
        planned_total = planned_totals.get(key, 0)
        if existing_total == planned_total:
            skipped_existing.extend(planned_by_key.get(key, ()))
            continue

        if existing_total < planned_total:
            deficit = planned_total - existing_total
            matched, unmatched = _matching_planned_blocks(
                planned_by_key.get(key, ()), existing_by_key.get(key, ())
            )
            skipped_existing.extend(matched)
            chosen = _deficit_blocks(unmatched, deficit)
            chosen_total = sum(_duration(block) for block in chosen)
            if chosen_total != deficit:
                diff_create_errors.append(
                    f"task {key[0]} @ {key[1]}: không tạo được đúng deficit "
                    f"{deficit}' (chỉ chọn được {chosen_total}')"
                )
            planned_creates.extend(chosen)
            if existing_total > 0 and chosen:
                recovered.extend(chosen)
                diff_warnings.append(
                    f"phục hồi ghi dở task {key[0]} @ {key[1]}: "
                    f"đã có {existing_total}', kế hoạch {planned_total}', tạo bù {deficit}'"
                )
            continue

        surplus = existing_total - planned_total
        existing_latest = _sort_latest(existing_by_key.get(key, ()))
        replacement = False
        if planned_total == 0:
            chosen_deletes = existing_latest
        else:
            matched, unmatched, matched_existing = _match_planned_blocks(
                planned_by_key.get(key, ()), existing_by_key.get(key, ())
            )
            skipped_existing.extend(matched)
            matched_existing_ids = {id(block) for block in matched_existing}
            unmatched_existing = [
                block
                for block in existing_latest
                if id(block) not in matched_existing_ids
            ]
            exact = _exact_subset(unmatched_existing, surplus)
            if exact is not None:
                chosen_deletes = tuple(exact)  # already latest-first
            else:
                chosen_deletes = _latest_cover(unmatched_existing, surplus)
                if chosen_deletes:
                    replacement = True
                    deleted_minutes = sum(_duration(block) for block in chosen_deletes)
                    retained_total = existing_total - deleted_minutes
                    deficit = max(0, planned_total - retained_total)
                    replacement_creates = _deficit_blocks(unmatched, deficit)
                    created_minutes = sum(
                        _duration(block) for block in replacement_creates
                    )
                    if created_minutes != deficit:
                        diff_create_errors.append(
                            f"task {key[0]} @ {key[1]}: không tạo được đúng deficit "
                            f"thay thế {deficit}' (chỉ chọn được {created_minutes}')"
                        )
                    planned_creates.extend(replacement_creates)
                    required_delete_ids = tuple(
                        block.event_id for block in chosen_deletes
                    )
                    for block in replacement_creates:
                        replacement_requirements[id(block)] = required_delete_ids
                    replacement_delete_candidates.update(
                        id(block) for block in chosen_deletes
                    )
                    diff_warnings.append(
                        f"task {key[0]} @ {key[1]}: surplus {surplus}' không khớp "
                        f"block nguyên vẹn; thay thế bằng xóa {deleted_minutes}' "
                        f"và tạo bù {deficit}' để đạt kế hoạch {planned_total}'"
                    )
                else:
                    diff_delete_errors.append(
                        f"task {key[0]} @ {key[1]}: surplus {surplus}' không khớp "
                        "độ dài block, không xóa được block thay thế an toàn"
                    )
                    diff_warnings.append(
                        f"task {key[0]} @ {key[1]}: lịch cũ dư "
                        f"{surplus}' nhưng chưa xóa vì không có block nguyên vẹn "
                        "để thay thế an toàn"
                    )
        planned_deletes.extend(chosen_deletes)

        if existing_total > planned_total and chosen_deletes:
            if not replacement:
                diff_warnings.append(
                    f"task {key[0]} @ {key[1]}: lịch cũ dư "
                    f"{existing_total - planned_total}' so với kế hoạch {planned_total}'"
                )

        if key[0] not in current_task_ids:
            diff_warnings.append(
                f"task {key[0]} @ {key[1]} không còn trong Todoist: block là ứng viên xóa"
            )

    # The order is significant: latest blocks are deleted first, while create
    # order follows the plan.  This also makes retries deterministic.
    planned_deletes.sort(key=lambda block: (block.start, block.end, block.event_id), reverse=True)

    total_auto_blocks = sum(
        1
        for block in current_existing
        if block.title.startswith(AUTO_BLOCK_PREFIX)
    )
    deletion_limit = max(3, total_auto_blocks * 0.5)

    if plan is not None and not plan.ok:
        target_id, writer_name, gate_errors = requested_id, None, []
    else:
        target_id, writer_name, gate_errors = _resolve_target(
            writer,
            requested_name,
            requested_id,
            planned_deletes,
        )

    delete_gate_errors = list(diff_delete_errors) + list(gate_errors)
    create_gate_errors = list(diff_create_errors) + list(gate_errors)
    errors = list(diff_delete_errors) + list(diff_create_errors) + list(gate_errors)

    def add_global_gate_error(message: str) -> None:
        errors.append(message)
        delete_gate_errors.append(message)
        create_gate_errors.append(message)

    def add_delete_gate_error(message: str) -> None:
        errors.append(message)
        delete_gate_errors.append(message)

    def add_create_gate_error(message: str) -> None:
        errors.append(message)
        create_gate_errors.append(message)

    if not window_supplied and (planned_deletes or planned_creates):
        add_global_gate_error("thiếu cửa sổ D1/D2 rõ ràng: không được xóa hoặc ghi")
    if requested_name != AUTO_STUDY_CALENDAR:
        add_global_gate_error(
            f"target calendar '{requested_name}' không đúng tên chính xác '{AUTO_STUDY_CALENDAR}'"
        )
    if plan is not None and not plan.ok:
        add_global_gate_error("PlanResult.ok=False: không được ghi hoặc xóa calendar")
    elif plan is None:
        add_global_gate_error("thiếu PlanResult.ok: không được ghi hoặc xóa calendar")

    if (planned_deletes or planned_creates) and not target_id:
        message = "không xác định được calendar_id chính xác của Auto-Study"
        errors.append(message)
        if planned_deletes:
            delete_gate_errors.append(message)
        if planned_creates:
            create_gate_errors.append(message)
    if len(planned_deletes) > deletion_limit:
        add_delete_gate_error(
            f"số block xóa {len(planned_deletes)} vượt ngưỡng "
            f"max(3, 50% tổng {total_auto_blocks}) = {deletion_limit:g}"
        )

    seen_delete_ids: set[str] = set()
    for block in planned_deletes:
        parsed_task_id = _task_id_from_description(getattr(block, "description", ""))
        if not block.event_id:
            add_delete_gate_error("delete candidate thiếu event_id")
        elif block.event_id in seen_delete_ids:
            add_delete_gate_error(f"delete candidate trùng event_id {block.event_id}")
        else:
            seen_delete_ids.add(block.event_id)
        if not block.title.startswith(AUTO_BLOCK_PREFIX):
            add_delete_gate_error(
                f"event {block.event_id} không bắt đầu bằng {AUTO_BLOCK_PREFIX}"
            )
        if parsed_task_id is None:
            add_delete_gate_error(
                f"event {block.event_id} thiếu task ID parseable trong description"
            )
        elif parsed_task_id != block.task_id:
            add_delete_gate_error(
                f"event {block.event_id}: task ID description '{parsed_task_id}' "
                f"khác task_id '{block.task_id}'"
            )
        if not _in_window(block, d1, d2):
            add_delete_gate_error(f"event {block.event_id} nằm ngoài cửa sổ D1/D2")
        if not block.calendar_id:
            add_delete_gate_error(f"event {block.event_id} thiếu calendar_id")
        elif target_id is not None and block.calendar_id != target_id:
            add_delete_gate_error(
                f"event {block.event_id} ở calendar_id '{block.calendar_id}', "
                f"không phải Auto-Study '{target_id}'"
            )
        block_calendar_name = getattr(block, "calendar_name", "")
        if block_calendar_name and block_calendar_name != AUTO_STUDY_CALENDAR:
            add_delete_gate_error(
                f"event {block.event_id} ở calendar '{block_calendar_name}', "
                f"không phải '{AUTO_STUDY_CALENDAR}'"
            )

    if not dry_run and (planned_deletes or planned_creates):
        if writer is None:
            add_global_gate_error("writer=None: không thể thực hiện reconciliation")
        else:
            if planned_deletes and not callable(getattr(writer, "delete_event", None)):
                add_delete_gate_error("writer không có delete_event(event_id, calendar_id)")
            if planned_creates and not callable(getattr(writer, "create_block", None)):
                add_create_gate_error("writer không có create_block(block, meta)")

    started_replacement = False
    if now is not None:
        for block in planned_deletes:
            if id(block) not in replacement_delete_candidates:
                continue
            try:
                started = block.start <= now
            except TypeError as exc:
                add_delete_gate_error(
                    f"không so sánh được now với replacement event {block.event_id}: {exc}"
                )
                continue
            if started:
                started_replacement = True
                diff_warnings.append(
                    f"replacement delete event {block.event_id} bị chặn: "
                    f"block đã bắt đầu (started) tại now={now.isoformat()}, không xóa"
                )

    gate_delete = not delete_gate_errors and not started_replacement
    gate_create = not create_gate_errors
    gate_passed = gate_delete and gate_create
    if not gate_delete and planned_deletes:
        diff_warnings.append(
            "delete gate bị chặn (gate_delete=False): bỏ qua toàn bộ delete; "
            "các create độc lập vẫn được xét"
        )
    if not gate_create and planned_creates:
        diff_warnings.append(
            "create gate bị chặn (gate_create=False): không tạo block nào"
        )

    deleted: list[DeleteOutcome] = []
    created: list[CreateOutcome] = []
    mismatch: list[str] = []
    delete_success: dict[str, bool] = {}
    side_effect_attempted = False

    if not dry_run and writer is not None:
        # Never interleave deletion and creation.  A failed delete must not
        # prevent later deletes or unrelated creates from being attempted.
        if gate_delete:
            for block in planned_deletes:
                side_effect_attempted = True
                try:
                    writer.delete_event(block.event_id, target_id or block.calendar_id)
                except Exception as exc:
                    error = str(exc) or type(exc).__name__
                    delete_success[block.event_id] = False
                    deleted.append(DeleteOutcome(block.event_id, False, error))
                    errors.append(f"delete event {block.event_id} lỗi: {error}")
                else:
                    delete_success[block.event_id] = True
                    deleted.append(DeleteOutcome(block.event_id, True, None))

        if gate_create:
            for block in planned_creates:
                required_delete_ids = replacement_requirements.get(id(block))
                if required_delete_ids:
                    if not gate_delete:
                        diff_warnings.append(
                            f"replacement create {block.task_id} @ {block.start} bị bỏ qua: "
                            "delete gate bị chặn"
                        )
                        continue
                    failed_delete_ids = tuple(
                        event_id
                        for event_id in required_delete_ids
                        if not delete_success.get(event_id, False)
                    )
                    if failed_delete_ids:
                        diff_warnings.append(
                            f"replacement create {block.task_id} @ {block.start} bị bỏ qua: "
                            f"delete bắt buộc thất bại ({', '.join(failed_delete_ids)})"
                        )
                        continue
                side_effect_attempted = True
                try:
                    event_id = writer.create_block(
                        block,
                        BlockMeta(task_id=block.task_id, source_id=block.task_id),
                    )
                except Exception as exc:
                    error = str(exc) or type(exc).__name__
                    created.append(CreateOutcome(block, None, error))
                    errors.append(
                        f"create block {block.task_id} @ {block.start} lỗi: {error}"
                    )
                else:
                    created.append(CreateOutcome(block, event_id, None))

    if side_effect_attempted:
        rb_start, rb_end = _readback_range(
            d1,
            d2,
            current_existing,
            current_planned,
            readback_start,
            readback_end,
        )
        try:
            readback = writer.read_back(rb_start, rb_end)
        except Exception as exc:
            error = str(exc) or type(exc).__name__
            errors.append(f"read_back lỗi: {error}")
            mismatch.append(f"không đọc lại được sau reconciliation: {error}")
        else:
            readback_in_window = [
                block
                for block in readback
                if _in_window(block, d1, d2)
                and block.title.startswith(AUTO_BLOCK_PREFIX)
                and (target_id is None or block.calendar_id == target_id)
            ]
            actual_totals = _totals(readback_in_window, d1, d2)
            for key in sorted(set(planned_totals) | set(actual_totals)):
                expected = planned_totals.get(key, 0)
                actual = actual_totals.get(key, 0)
                if expected != actual:
                    mismatch.append(
                        f"task {key[0]} @ {key[1]}: sau ghi {actual}' "
                        f"!= kế hoạch {expected}'"
                    )

    return ReconciliationReport(
        planned_deletes=tuple(planned_deletes),
        planned_creates=tuple(planned_creates),
        deleted=tuple(deleted),
        created=tuple(created),
        skipped_existing=tuple(skipped_existing),
        recovered=tuple(recovered),
        warnings=tuple(diff_warnings),
        errors=tuple(errors),
        mismatch=tuple(mismatch),
        gate_passed=gate_passed,
        gate_delete=gate_delete,
        gate_create=gate_create,
        total_auto_blocks=total_auto_blocks,
        deletion_limit=deletion_limit,
        target_calendar_id=target_id,
    )


# Explicit aliases for callers that prefer naming the operation rather than
# the module's main verb.
reconcile_blocks = reconcile
diff_reconcile = reconcile


__all__ = [
    "CreateOutcome",
    "CreateResult",
    "DeleteOutcome",
    "DeleteResult",
    "ReconcileReport",
    "ReconciliationReport",
    "ReconciliationResult",
    "diff_reconcile",
    "reconcile",
    "reconcile_blocks",
]
