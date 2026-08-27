"""Runner: gắn kết đọc → build_plan → ghi → báo cáo. 11 bước bắt buộc."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Sequence
from zoneinfo import ZoneInfo

from horae.adapters.protocols import AutoBlock, CalendarReader, CalendarWriter, TaskSource
from horae.adapters.parsing import ParseResult, parse_tasks
from horae.reconcile import ReconciliationReport, reconcile
from horae.state.ledger import build_ledger
from scheduler_core.config import SchedulerConfig
from scheduler_core.models import Assignment, Block, FixedEvent, OngoingTask
from scheduler_core.plan import PlanResult, build_plan


# ---------------------------------------------------------------- report


@dataclass(frozen=True)
class CreatedBlock:
    """Kết quả tạo một block: block định tạo + event id (hoặc None nếu lỗi)."""

    block: Block
    event_id: str | None
    error: str | None = None


@dataclass(frozen=True)
class RunReport:
    today: date
    dry_run: bool
    parse: ParseResult
    plan: PlanResult
    created: tuple[CreatedBlock, ...]
    skipped_existing: tuple[Block, ...]  # block bỏ qua vì tổng khớp
    recovered: tuple[Block, ...]         # block tạo bù sau ghi dở (existing < planned)
    readback_mismatch: tuple[str, ...]   # mô tả lệch giữa định tạo và đọc lại
    warnings: tuple[str, ...]
    errors: tuple[str, ...]   # check severity error fail (khi ok=False)
    reconciliation: ReconciliationReport | None = None

    @property
    def reconcile(self) -> ReconciliationReport | None:
        """Alias for callers that use the shorter operation name."""
        return self.reconciliation

    @property
    def reconciliation_report(self) -> ReconciliationReport | None:
        return self.reconciliation

    @property
    def deleted(self):
        return self.reconciliation.deleted if self.reconciliation is not None else ()

    @property
    def planned_deletes(self):
        return self.reconciliation.planned_deletes if self.reconciliation is not None else ()

    @property
    def planned_creates(self):
        return self.reconciliation.planned_creates if self.reconciliation is not None else ()

    @property
    def gate_passed(self) -> bool:
        return self.reconciliation.gate_passed if self.reconciliation is not None else False

    @property
    def gate_delete(self) -> bool:
        return self.reconciliation.gate_delete if self.reconciliation is not None else False

    @property
    def gate_create(self) -> bool:
        return self.reconciliation.gate_create if self.reconciliation is not None else False

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


# ---------------------------------------------------------------- run


def run(
    today: date,
    sources: Sequence[TaskSource],
    reader: CalendarReader,
    writer: CalendarWriter | None,
    config: SchedulerConfig,
    *,
    dry_run: bool = True,
    now: datetime | None = None,
) -> RunReport:
    """Trình tự 11 bước. Không raise vì lịch xấu (ok=False → không ghi, báo cáo)."""
    tz = ZoneInfo(config.timezone)
    d1 = today + timedelta(days=1)
    write_end = d1 + timedelta(days=config.write_horizon_days - 1)
    before = datetime.combine(d1, datetime.min.time(), tzinfo=tz)  # 00:00 D1

    all_warns: list[str] = []

    # 1. Đọc task → phân loại
    raw_tasks: list = []
    for src in sources:
        raw_tasks.extend(src.fetch_open_tasks())
    parse = parse_tasks(raw_tasks, config)
    all_warns.extend(parse.warnings)

    # 2. Đọc event trong cửa sổ (một lần cho cả khoảng)
    range_start = datetime.combine(d1, datetime.min.time(), tzinfo=tz)
    range_end = datetime.combine(write_end + timedelta(days=1), datetime.min.time(), tzinfo=tz)
    raw_events = reader.list_events(range_start, range_end)

    # Nhóm theo ngày
    events_by_day: dict[date, list[FixedEvent]] = {}
    from horae.adapters.gcal import GoogleCalendarReader
    if isinstance(reader, GoogleCalendarReader):
        fixed = reader.to_fixed_events(raw_events)
    else:
        fixed = _to_fixed_events_generic(raw_events, tz, all_warns)
    for fe in fixed:
        events_by_day.setdefault(fe.start.date(), []).append(fe)

    # 3. Đọc block [Auto] quá khứ → build_ledger → gán done_minutes
    auto_past = reader.list_auto_blocks(range_start, range_end)
    ledger = build_ledger(auto_past, before)
    assignments = [
        Assignment(
            task_id=a.task_id,
            title=a.title,
            estimate_minutes=a.estimate_minutes,
            deadline=a.deadline,
            done_minutes=ledger.get(a.task_id, 0),
        )
        for a in parse.assignments
    ]

    # 4. Đọc block [Auto] đã có trong D1/D2 → existing_blocks
    existing_blocks: dict[date, int] = {}
    for block in auto_past:
        if block.start.date() >= d1 and block.start.date() <= write_end:
            existing_blocks[block.start.date()] = existing_blocks.get(block.start.date(), 0) + _duration(block)

    # 5. build_plan
    plan = build_plan(today, assignments, parse.ongoing, events_by_day, existing_blocks, config)
    all_warns.extend(list(plan.warnings))

    # 6. Diff + safety gate.  The reconciliation module also checks plan.ok;
    # doing the diff before returning is important for dry-run diagnostics.
    error_checks = [c for c in plan.checks if c.severity == "error" and not c.passed]
    reconcile_report = reconcile(
        plan,
        auto_past,
        {str(raw.task_id) for raw in raw_tasks if raw.task_id},
        d1,
        write_end,
        writer=writer,
        dry_run=dry_run,
        readback_start=range_start,
        readback_end=range_end,
        now=now,
    )
    all_warns.extend(reconcile_report.warnings)

    plan_errors = [f"{c.name}: {c.detail}" for c in error_checks]
    reconcile_errors = list(reconcile_report.errors)
    if not plan.ok:
        reconcile_errors = [
            error
            for error in reconcile_errors
            if not error.startswith("PlanResult.ok=False:")
        ]
    all_errors = plan_errors + reconcile_errors
    created = tuple(
        CreatedBlock(result.block, result.event_id, result.error)
        for result in reconcile_report.created
    )

    # 7-11. Reconciliation owns ordering, side effects, and read-back.
    return RunReport(
        today=today,
        dry_run=dry_run,
        parse=parse,
        plan=plan,
        created=created,
        skipped_existing=reconcile_report.skipped_existing,
        recovered=reconcile_report.recovered,
        readback_mismatch=reconcile_report.mismatch,
        warnings=tuple(all_warns),
        errors=tuple(all_errors),
        reconciliation=reconcile_report,
    )


def _duration(block: AutoBlock) -> int:
    return int((block.end - block.start).total_seconds() // 60)


def _to_fixed_events_generic(raw_events: Sequence, tz: ZoneInfo, warns: list[str]) -> list[FixedEvent]:
    """Quy đổi RawEvent → FixedEvent cho reader không phải GoogleCalendarReader."""
    from horae.adapters.gcal import _detect_travel
    fixed: list[FixedEvent] = []
    for ev in raw_events:
        if ev.is_all_day or not ev.is_opaque:
            continue
        travel, w = _detect_travel(ev.description, ev.event_id)
        warns.extend(w)
        start = ev.start.astimezone(tz) if ev.start.tzinfo else ev.start.replace(tzinfo=tz)
        end = ev.end.astimezone(tz) if ev.end.tzinfo else ev.end.replace(tzinfo=tz)
        fixed.append(
            FixedEvent(
                title=ev.title, start=start, end=end,
                requires_travel=travel, is_opaque=ev.is_opaque, is_all_day=ev.is_all_day,
            )
        )
    return fixed
