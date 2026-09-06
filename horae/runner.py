"""Runner: gắn kết đọc → build_plan → ghi → báo cáo. 11 bước bắt buộc."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Sequence
from zoneinfo import ZoneInfo

from horae.adapters.protocols import AutoBlock, CalendarReader, CalendarWriter, TaskSource
from horae.adapters.parsing import ParseResult, parse_tasks
from horae.reconcile import ReconciliationReport, reconcile
from horae.state.ledger import build_ledger
from scheduler_core.config import SchedulerConfig
from scheduler_core.models import Assignment, Block, CheckResult, FixedEvent, OngoingTask
from scheduler_core.plan import PlanResult, build_plan

if TYPE_CHECKING:  # chỉ để chú thích kiểu — runner không nạp horae.llm lúc chạy
    from horae.adapters.parsing import TaskClassification
    from horae.llm.registry import LLMClient
    from horae.state.store import LocalStore


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
    def classifications(self) -> tuple["TaskClassification", ...]:
        """Bảng "Todoist — phân loại": task nào, ước lượng bao nhiêu, từ nguồn nào."""
        return self.parse.classifications

    @property
    def overdue_task_ids(self) -> tuple[str, ...]:
        """Task deadline đã trôi qua — cần người dùng dọn, không phải lỗi lịch."""
        return self.plan.overdue_task_ids

    @property
    def estimate_sources(self) -> dict[str, str]:
        """{task_id: "title" | "description" | "llm" | "default"}."""
        return self.parse.estimate_sources

    def classification_table(self) -> str:
        """Bảng phân loại dạng text cho báo cáo dry-run.

        Cột ``source`` là điểm chính: "60 phút vì LLM đoán vậy" và "60 phút vì
        không đoán được gì cả" cần mức tin tưởng khác nhau.
        """
        header = ("task_id", "kind", "phút", "source", "title")
        rows = [
            (row.task_id, row.kind, str(row.estimate_minutes), row.source, row.title)
            for row in self.classifications
        ]
        for task_id in self.parse.skipped:
            rows.append((task_id, "skipped", "-", "-", ""))
        widths = [
            max(len(header[i]), *(len(r[i]) for r in rows)) if rows else len(header[i])
            for i in range(len(header))
        ]
        lines = ["Todoist — phân loại"]
        lines.append("  ".join(h.ljust(w) for h, w in zip(header, widths)).rstrip())
        lines.append("  ".join("-" * w for w in widths))
        for row in rows:
            lines.append("  ".join(c.ljust(w) for c, w in zip(row, widths)).rstrip())
        return "\n".join(lines)

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
    llm: "LLMClient | None" = None,
    store: "LocalStore | None" = None,
    dry_run: bool = True,
    now: datetime | None = None,
) -> RunReport:
    """Trình tự 11 bước. Không raise vì lịch xấu (ok=False → không ghi, báo cáo).

    ``llm`` mặc định None → không có LLM trong đường chính. runner KHÔNG tự
    dựng ``LLMClient`` từ biến môi trường: việc đọc ``LLMConfig`` và chọn
    provider là của nơi gọi ``run()`` (script/REPL của người vận hành), để
    "có dùng LLM hay không" là quyết định ở tầng cấu hình và để runner test
    được mà không phụ thuộc môi trường thật. ``store`` cũng vậy: runner không
    tự tạo thư mục cache, nhưng truyền xuống ``parse_tasks`` để cache theo
    nội dung hoạt động trên đường thật.

    ``now`` là **bắt buộc khi ghi thật** (``dry_run=False``): nó bảo vệ cơ chế
    không-xóa-block-đã-bắt-đầu trong ``reconcile``, vốn chỉ chạy khi ``now``
    khác None. Thiếu nó ở chế độ ghi → dừng ngay, không chạm calendar.
    """
    # Cổng cấu hình, chạy TRƯỚC mọi lời gọi mạng: ghi thật bằng ước lượng LLM
    # không cache là chạy đúng cấu hình phi xác định nhất — mỗi đêm một con số
    # khác cho cùng một task chưa đổi nội dung. Với dry_run=True thì chỉ cảnh
    # báo (parse_tasks lo), vì không ghi gì cả.
    if llm is not None and store is None and not dry_run:
        return _blocked_report(
            today,
            dry_run,
            "llm_without_store",
            "ghi thật với llm nhưng thiếu store: ước lượng LLM sẽ không được "
            "cache, mỗi lần chạy cho một con số khác nhau",
            "Dừng trước khi lập kế hoạch: dry_run=False và có llm nhưng thiếu "
            "store, không task nào được xử lý, không chạm calendar",
        )

    # Cổng cấu hình thứ hai: ghi thật BẮT BUỘC có `now`. Không phải vì nhãn
    # hiển thị, mà vì `reconcile` chỉ chạy kiểm "delete bị chặn do block đã
    # bắt đầu" (áp cho MỌI delete, không riêng replacement) khi `now is not
    # None` — thiếu `now` là tắt cơ chế chống xóa block đang chạy dở. Với
    # dry_run=True chỉ cảnh báo (không xóa gì).
    if now is None and not dry_run:
        return _blocked_report(
            today,
            dry_run,
            "now_required_for_write",
            "ghi thật nhưng thiếu now: reconcile sẽ bỏ qua kiểm 'block đã bắt "
            "đầu thì không xóa', và nhãn overdue/infeasible tính theo 00:00 ngày chạy",
            "Dừng trước khi lập kế hoạch: dry_run=False nhưng thiếu now, "
            "không task nào được xử lý, không chạm calendar",
        )

    tz = ZoneInfo(config.timezone)
    d1 = today + timedelta(days=1)
    write_end = d1 + timedelta(days=config.write_horizon_days - 1)
    before = datetime.combine(d1, datetime.min.time(), tzinfo=tz)  # 00:00 D1

    all_warns: list[str] = []

    if now is None:
        # Im lặng quay về 00:00 là kiểu lỗi dễ quên nhất: nhãn sai lệch có hệ
        # thống mỗi lần chạy buổi chiều/tối, và reconcile mất luôn kiểm
        # "block đã bắt đầu thì không xóa" (nó chỉ chạy khi now khác None).
        all_warns.append(
            "now=None: nhãn overdue/infeasible tính theo 00:00 ngày chạy "
            "(task hạn sớm hơn trong hôm nay sẽ hiện là infeasible thay vì overdue), "
            "và reconcile BỎ QUA kiểm 'block đã bắt đầu thì không xóa'. "
            "Truyền now=datetime.now(ZoneInfo(config.timezone)) khi chạy thật."
        )

    # 1. Đọc task → phân loại
    raw_tasks: list = []
    for src in sources:
        raw_tasks.extend(src.fetch_open_tasks())
    if llm is None:
        # Đường không-LLM: y hệt trước khi nối, và không nạp horae.llm.
        parse = parse_tasks(raw_tasks, config, None, store)
    else:
        from horae.llm.protocol import LLMBadRequestError

        try:
            parse = parse_tasks(raw_tasks, config, llm, store)
        except LLMBadRequestError as exc:
            # Prompt sai là lỗi CỦA TA, không phải lỗi provider: dừng ngay,
            # không xử lý task nào tiếp, không chạm writer. Vẫn trả về một báo
            # cáo để lần dry-run không mất sạch thông tin ngoài traceback.
            return _blocked_report(
                today,
                dry_run,
                "llm_bad_request",
                f"{type(exc).__name__}: {exc}",
                "Dừng trước khi lập kế hoạch: prompt LLM sai (lỗi cấu hình của ta), "
                "không task nào được xử lý, không chạm calendar",
            )
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
    plan = build_plan(today, assignments, parse.ongoing, events_by_day, existing_blocks, config, now)
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


def _blocked_report(
    today: date, dry_run: bool, check_name: str, detail: str, warning: str
) -> RunReport:
    """Báo cáo cho ca dừng sớm: không plan, không ghi, nhưng có dấu vết.

    ``plan.ok=False`` nên mọi kiểm tra "chạy được không" của caller đều fail,
    và ``errors`` nêu rõ lý do dừng chứ không lẫn với lịch xấu. Không gọi
    ``reconcile`` lần nào nên không thể có side effect.
    """
    from horae.adapters.parsing import ParseResult

    line = f"{check_name}: {detail}"
    empty_plan = PlanResult(
        blocks=[],
        projected={},
        capacities={},
        checks=[CheckResult(check_name, False, detail, severity="error")],
        ok=False,
        warnings=(),
        completed_task_ids=(),
        infeasible_task_ids=(),
        shortfall={},
    )
    return RunReport(
        today=today,
        dry_run=dry_run,
        parse=ParseResult(assignments=(), ongoing=(), skipped=(), warnings=()),
        plan=empty_plan,
        created=(),
        skipped_existing=(),
        recovered=(),
        readback_mismatch=(),
        warnings=(warning,),
        errors=(line,),
        reconciliation=None,
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
