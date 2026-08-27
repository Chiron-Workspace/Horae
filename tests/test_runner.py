"""Test cho horae.runner — dùng fake source/reader/writer hoàn toàn."""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from horae.adapters.protocols import AutoBlock, BlockMeta, RawEvent, RawTask
from horae.runner import run, RunReport
from scheduler_core.config import DEFAULT_CONFIG, PRESET_STUDENT_VN, SchedulerConfig
from scheduler_core.models import Assignment, Block, FixedEvent, OngoingTask
from tests.fakes import FakeCalendarReader, FakeCalendarWriter, FakeTaskSource

TZ = ZoneInfo(PRESET_STUDENT_VN.timezone)
TODAY = date(2026, 1, 4)  # Sunday → D1 = Monday 2026-01-05


def _raw_task(tid, title, *, due=None, labels=()):
    return RawTask(task_id=tid, title=title, description="", labels=labels, due=due)


def _fixed_event(day, sh, sm, eh, em, title, *, travel=False):
    return FixedEvent(
        title=title,
        start=datetime.combine(day, time(sh, sm), tzinfo=TZ),
        end=datetime.combine(day, time(eh, em), tzinfo=TZ),
        requires_travel=travel,
    )


def _auto_block(eid, task_id, day, sh, sm, eh, em, kind="assignment"):
    return AutoBlock(
        event_id=eid, task_id=task_id, title=f"[Auto] [{kind}] {task_id}",
        start=datetime.combine(day, time(sh, sm), tzinfo=TZ),
        end=datetime.combine(day, time(eh, em), tzinfo=TZ),
        kind=kind, calendar_id="auto-study",
    )


def _raw_event(eid, title, day, sh, sm, eh, em, *, desc="", opaque=True, all_day=False, cal_id="school"):
    return RawEvent(
        event_id=eid, title=title,
        start=datetime.combine(day, time(sh, sm), tzinfo=TZ),
        end=datetime.combine(day, time(eh, em), tzinfo=TZ),
        description=desc, is_opaque=opaque, is_all_day=all_day, calendar_id=cal_id,
    )


def _basic_setup(*, past_blocks=(), existing_blocks=(), events=(), tasks=None):
    """Setup cơ bản: 1 assignment Vật Lí 180' hạn Wed, 1 ongoing IELTS 60'/ngày."""
    if tasks is None:
        tasks = [
            _raw_task("vatli", "Làm BTVN Vật Lí [180m]", due=date(2026, 1, 7)),
            _raw_task("ielts", "Ôn IELTS [60m/ngày]", labels=("ontap",)),
        ]
    source = FakeTaskSource(tasks)
    reader = FakeCalendarReader(
        events=events,
        auto_blocks=list(past_blocks) + list(existing_blocks),
    )
    return source, reader


def _run(writer=None, *, dry_run=True, **kw):
    source, reader = _basic_setup(**kw)
    return run(TODAY, [source], reader, writer, PRESET_STUDENT_VN, dry_run=dry_run)


# ---------------------------------------------------------------- dry_run


def test_31_dry_run_no_writer_calls():
    """dry_run=True → writer KHÔNG được gọi lần nào."""
    writer = FakeCalendarWriter()
    report = _run(writer=writer, dry_run=True)
    assert len(writer.created) == 0
    assert report.dry_run is True
    assert len(report.created) == 0


# ---------------------------------------------------------------- ok=False


def test_32_ok_false_writer_not_called(monkeypatch):
    """ok=False → writer KHÔNG được gọi, report nêu tên check hỏng."""
    import horae.runner as runner_mod

    # Monkeypatch build_plan để trả plan có error check fail
    from scheduler_core.plan import PlanResult
    from scheduler_core.models import CheckResult, DayCapacity, Interval

    fake_plan = PlanResult(
        blocks=[], projected={}, capacities={},
        checks=[CheckResult("block_size", False, "fake fail", severity="error")],
        ok=False, warnings=(), completed_task_ids=(), infeasible_task_ids=(), shortfall={},
    )
    monkeypatch.setattr(runner_mod, "build_plan", lambda *a, **kw: fake_plan)

    writer = FakeCalendarWriter()
    source, reader = _basic_setup()
    report = run(TODAY, [source], reader, writer, PRESET_STUDENT_VN, dry_run=False)
    assert len(writer.created) == 0
    assert report.plan.ok is False
    assert any("block_size" in e for e in report.errors)


# ---------------------------------------------------------------- ok=True + warnings

def test_33_ok_with_warnings_writer_called(monkeypatch):
    """ok=True + warnings → writer ĐƯỢC gọi, warnings có trong report."""
    import horae.runner as runner_mod
    from scheduler_core.plan import PlanResult
    from scheduler_core.models import CheckResult, DayCapacity, Block

    d1 = TODAY + timedelta(days=1)
    fake_block = Block(
        task_id="vatli", title="Làm BTVN Vật Lí", kind="assignment",
        start=datetime.combine(d1, time(7, 0), tzinfo=TZ),
        end=datetime.combine(d1, time(8, 0), tzinfo=TZ),
    )
    fake_plan = PlanResult(
        blocks=[fake_block], projected={}, capacities={},
        checks=[
            CheckResult("block_size", True, "OK"),
            CheckResult("no_day_dominance", False, "dồn quá", severity="warning"),
        ],
        ok=True, warnings=("no_day_dominance",),
        completed_task_ids=(), infeasible_task_ids=(), shortfall={},
    )
    monkeypatch.setattr(runner_mod, "build_plan", lambda *a, **kw: fake_plan)

    writer = FakeCalendarWriter()
    source, reader = _basic_setup()
    report = run(TODAY, [source], reader, writer, PRESET_STUDENT_VN, dry_run=False)
    assert len(writer.created) == 1  # writer được gọi
    assert "no_day_dominance" in report.warnings
    assert report.plan.ok is True


# ---------------------------------------------------------------- chống trùng


def test_34_existing_block_skipped(monkeypatch):
    """Block cùng task_id cùng ngày đã tồn tại với TỔNG KHỚP → bỏ qua cả hai."""
    import horae.runner as runner_mod
    from scheduler_core.plan import PlanResult
    from scheduler_core.models import CheckResult, Block

    d1 = TODAY + timedelta(days=1)
    # Plan định tạo 2 block cho vatli @ D1 (30+30=60')
    fake_blocks = [
        Block(task_id="vatli", title="Vật Lí", kind="assignment",
              start=datetime.combine(d1, time(7, 0), tzinfo=TZ),
              end=datetime.combine(d1, time(7, 30), tzinfo=TZ)),
        Block(task_id="vatli", title="Vật Lí", kind="assignment",
              start=datetime.combine(d1, time(8, 0), tzinfo=TZ),
              end=datetime.combine(d1, time(8, 30), tzinfo=TZ)),
    ]
    fake_plan = PlanResult(
        blocks=fake_blocks, projected={}, capacities={},
        checks=[CheckResult("block_size", True, "OK")],
        ok=True, warnings=(),
        completed_task_ids=(), infeasible_task_ids=(), shortfall={},
    )
    monkeypatch.setattr(runner_mod, "build_plan", lambda *a, **kw: fake_plan)

    # Block đã tồn tại: 60' cho vatli @ D1 (khớp tổng 30+30=60')
    existing = [_auto_block("old1", "vatli", d1, 7, 0, 8, 0)]  # 60'
    source, reader = _basic_setup(existing_blocks=existing)
    writer = FakeCalendarWriter()
    report = run(TODAY, [source], reader, writer, PRESET_STUDENT_VN, dry_run=False)
    # Tổng existing (60') >= tổng planned (60') → bỏ qua cả hai
    assert len(writer.created) == 0
    assert len(report.skipped_existing) == 2


# ---------------------------------------------------------------- readback mismatch


def test_35_readback_mismatch_reported(monkeypatch):
    """Đọc lại lệch với danh sách định tạo → report đánh dấu lệch, không raise."""
    import horae.runner as runner_mod
    from scheduler_core.plan import PlanResult
    from scheduler_core.models import CheckResult, Block

    d1 = TODAY + timedelta(days=1)
    fake_block = Block(
        task_id="vatli", title="Vật Lí", kind="assignment",
        start=datetime.combine(d1, time(7, 0), tzinfo=TZ),
        end=datetime.combine(d1, time(8, 0), tzinfo=TZ),
    )
    fake_plan = PlanResult(
        blocks=[fake_block], projected={}, capacities={},
        checks=[CheckResult("block_size", True, "OK")],
        ok=True, warnings=(),
        completed_task_ids=(), infeasible_task_ids=(), shortfall={},
    )
    monkeypatch.setattr(runner_mod, "build_plan", lambda *a, **kw: fake_plan)

    # Writer tạo thành công nhưng read_back trả rỗng → mismatch
    source, reader = _basic_setup()

    class MismatchWriter(FakeCalendarWriter):
        def read_back(self, start, end):
            return ()  # rỗng → lệch

    writer = MismatchWriter()
    report = run(TODAY, [source], reader, writer, PRESET_STUDENT_VN, dry_run=False)
    assert len(report.readback_mismatch) > 0
    # Không raise — report vẫn trả về bình thường
    assert report.plan.ok is True


# ---------------------------------------------------------------- writer fail mid-way


def test_36_writer_fail_midway(monkeypatch):
    """Writer raise giữa chừng (block 2/3 lỗi) → report ghi rõ, không raise ra ngoài."""
    import horae.runner as runner_mod
    from scheduler_core.plan import PlanResult
    from scheduler_core.models import CheckResult, Block

    d1 = TODAY + timedelta(days=1)
    fake_blocks = [
        Block(task_id=f"t{i}", title=f"Task {i}", kind="assignment",
              start=datetime.combine(d1, time(7 + i, 0), tzinfo=TZ),
              end=datetime.combine(d1, time(7 + i + 1, 0), tzinfo=TZ))
        for i in range(3)
    ]
    fake_plan = PlanResult(
        blocks=fake_blocks, projected={}, capacities={},
        checks=[CheckResult("block_size", True, "OK")],
        ok=True, warnings=(),
        completed_task_ids=(), infeasible_task_ids=(), shortfall={},
    )
    monkeypatch.setattr(runner_mod, "build_plan", lambda *a, **kw: fake_plan)

    source, reader = _basic_setup()
    writer = FakeCalendarWriter(fail_on=1)  # block index 1 (thứ 2) raise
    report = run(TODAY, [source], reader, writer, PRESET_STUDENT_VN, dry_run=False)
    # Block 0 tạo OK, block 1 lỗi, block 2 tạo OK
    assert len(report.created) == 3
    assert report.created[0].error is None
    assert report.created[1].error is not None
    assert report.created[2].error is None


# ---------------------------------------------------------------- idempotent


def test_37_idempotent_two_runs():
    """Chạy hai lần liên tiếp với cùng state → lần hai tạo 0 block."""
    from datetime import timedelta
    d1 = TODAY + timedelta(days=1)
    d2 = d1 + timedelta(days=1)

    # Setup: Vật Lí 180' hạn Wed, IELTS 60'/ngày, không có event
    tasks = [
        _raw_task("vatli", "Làm BTVN Vật Lí [180m]", due=date(2026, 1, 7)),
        _raw_task("ielts", "Ôn IELTS [60m/ngày]", labels=("ontap",)),
    ]
    source = FakeTaskSource(tasks)
    reader = FakeCalendarReader(events=[], auto_blocks=[])
    writer = FakeCalendarWriter()

    # Lần 1
    report1 = run(TODAY, [source], reader, writer, PRESET_STUDENT_VN, dry_run=False)
    assert len(report1.created) > 0  # tạo block

    # Cập nhật reader với block đã tạo (giả lập calendar đã có)
    created_auto = writer.read_back(
        datetime.combine(d1, time(0, 0), tzinfo=TZ),
        datetime.combine(d2 + timedelta(days=1), time(0, 0), tzinfo=TZ),
    )
    reader2 = FakeCalendarReader(events=[], auto_blocks=list(created_auto))
    writer2 = FakeCalendarWriter()

    # Lần 2: cùng state, giờ có block trong D1/D2
    report2 = run(TODAY, [source], reader2, writer2, PRESET_STUDENT_VN, dry_run=False)
    assert len(writer2.created) == 0  # không tạo thêm


# ---------------------------------------------------------------- phục hồi ghi dở


def test_38_recovery_after_partial_write(monkeypatch):
    """Writer lỗi sau block đầu → chạy lại → block còn lại được tạo bù, report ghi 'phục hồi'."""
    import horae.runner as runner_mod
    from scheduler_core.plan import PlanResult
    from scheduler_core.models import CheckResult, Block

    d1 = TODAY + timedelta(days=1)
    # Plan định tạo 3 block cho vatli @ D1 (30+30+30=90')
    fake_blocks = [
        Block(task_id="vatli", title="Vật Lí", kind="assignment",
              start=datetime.combine(d1, time(7, 0), tzinfo=TZ),
              end=datetime.combine(d1, time(7, 30), tzinfo=TZ)),
        Block(task_id="vatli", title="Vật Lí", kind="assignment",
              start=datetime.combine(d1, time(8, 0), tzinfo=TZ),
              end=datetime.combine(d1, time(8, 30), tzinfo=TZ)),
        Block(task_id="vatli", title="Vật Lí", kind="assignment",
              start=datetime.combine(d1, time(9, 0), tzinfo=TZ),
              end=datetime.combine(d1, time(9, 30), tzinfo=TZ)),
    ]
    fake_plan = PlanResult(
        blocks=fake_blocks, projected={}, capacities={},
        checks=[CheckResult("block_size", True, "OK")],
        ok=True, warnings=(),
        completed_task_ids=(), infeasible_task_ids=(), shortfall={},
    )
    monkeypatch.setattr(runner_mod, "build_plan", lambda *a, **kw: fake_plan)

    # Lần 1: writer lỗi sau block 0, và tiếp tục lỗi → chỉ tạo được 30'
    source, reader = _basic_setup()
    writer1 = FakeCalendarWriter(fail_on=1, fail_all_after=True)
    report1 = run(TODAY, [source], reader, writer1, PRESET_STUDENT_VN, dry_run=False)
    assert len(writer1.created) == 1  # chỉ block 0 thành công, block 1+ fail

    # Lần 2: đọc lại thấy 30' đã có, plan định 90' → thiếu 60' → tạo bù
    existing = [
        AutoBlock(
            event_id="ev0", task_id="vatli",
            title="[Auto] [assignment] Vật Lí",
            start=datetime.combine(d1, time(7, 0), tzinfo=TZ),
            end=datetime.combine(d1, time(7, 30), tzinfo=TZ),
            kind="assignment", calendar_id="auto-study",
        ),
    ]
    reader2 = FakeCalendarReader(events=[], auto_blocks=list(existing))
    writer2 = FakeCalendarWriter()
    monkeypatch.setattr(runner_mod, "build_plan", lambda *a, **kw: fake_plan)
    source2, _ = _basic_setup()
    report2 = run(TODAY, [source2], reader2, writer2, PRESET_STUDENT_VN, dry_run=False)

    # 2 block còn lại (30+30=60') được tạo bù
    assert len(writer2.created) == 2
    # Report ghi rõ đây là phục hồi
    assert len(report2.recovered) == 2


def test_39_three_run_diff_deletion_lifecycle():
    """Tạo 2 block → giảm estimate xóa block muộn → hoàn thành task xóa block còn lại."""
    d1 = TODAY + timedelta(days=1)
    d2 = d1 + timedelta(days=1)
    tz = ZoneInfo(DEFAULT_CONFIG.timezone)

    def task(estimate):
        return RawTask(
            task_id="t1",
            title=f"Task [{estimate}m]",
            description="",
            labels=(),
            due=datetime.combine(d2, time(21, 0), tzinfo=tz),
        )

    def reader_for(writer):
        start = datetime.combine(d1, time(0, 0), tzinfo=tz)
        end = datetime.combine(d2 + timedelta(days=1), time(0, 0), tzinfo=tz)
        return FakeCalendarReader(auto_blocks=list(writer.read_back(start, end)))

    writer = FakeCalendarWriter()

    # Lần 1: 180' branch A → 90' ở D1 và 90' ở D2.
    first = run(
        TODAY,
        [FakeTaskSource([task(180)])],
        reader_for(writer),
        writer,
        DEFAULT_CONFIG,
        dry_run=False,
    )
    assert [entry.block.duration_minutes for entry in first.created] == [90, 90]
    assert first.deleted == ()

    # Lần 2: giảm còn 90' branch B → giữ block sớm D1, xóa block muộn D2.
    second = run(
        TODAY,
        [FakeTaskSource([task(90)])],
        reader_for(writer),
        writer,
        DEFAULT_CONFIG,
        dry_run=False,
    )
    assert [entry.event_id for entry in second.planned_deletes] == ["fake-event-2"]
    assert [entry.event_id for entry in second.deleted if entry.success] == ["fake-event-2"]
    assert second.created == ()
    assert [block.event_id for block in reader_for(writer).list_auto_blocks(*(
        datetime.combine(d1, time(0, 0), tzinfo=tz),
        datetime.combine(d2 + timedelta(days=1), time(0, 0), tzinfo=tz),
    ))] == ["fake-event-1"]

    # Lần 3: task hoàn thành/biến mất khỏi Todoist → planned=0, xóa block còn lại.
    third = run(
        TODAY,
        [FakeTaskSource([])],
        reader_for(writer),
        writer,
        DEFAULT_CONFIG,
        dry_run=False,
    )
    assert [entry.event_id for entry in third.planned_deletes] == ["fake-event-1"]
    assert [entry.event_id for entry in third.deleted if entry.success] == ["fake-event-1"]
    assert third.created == ()
    assert reader_for(writer).list_auto_blocks(
        datetime.combine(d1, time(0, 0), tzinfo=tz),
        datetime.combine(d2 + timedelta(days=1), time(0, 0), tzinfo=tz),
    ) == ()
