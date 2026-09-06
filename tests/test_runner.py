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
# `now` bắt buộc khi dry_run=False. Trưa ngày chạy: mọi block đều ở D1+ nên
# kiểm "block đã bắt đầu" không đổi hành vi của các test cũ.
NOW = datetime.combine(TODAY, time(12, 0), tzinfo=TZ)


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
    report = run(TODAY, [source], reader, writer, PRESET_STUDENT_VN, dry_run=False, now=NOW)
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
    report = run(TODAY, [source], reader, writer, PRESET_STUDENT_VN, dry_run=False, now=NOW)
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
    report = run(TODAY, [source], reader, writer, PRESET_STUDENT_VN, dry_run=False, now=NOW)
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
    report = run(TODAY, [source], reader, writer, PRESET_STUDENT_VN, dry_run=False, now=NOW)
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
    report = run(TODAY, [source], reader, writer, PRESET_STUDENT_VN, dry_run=False, now=NOW)
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
    report1 = run(TODAY, [source], reader, writer, PRESET_STUDENT_VN, dry_run=False, now=NOW)
    assert len(report1.created) > 0  # tạo block

    # Cập nhật reader với block đã tạo (giả lập calendar đã có)
    created_auto = writer.read_back(
        datetime.combine(d1, time(0, 0), tzinfo=TZ),
        datetime.combine(d2 + timedelta(days=1), time(0, 0), tzinfo=TZ),
    )
    reader2 = FakeCalendarReader(events=[], auto_blocks=list(created_auto))
    writer2 = FakeCalendarWriter()

    # Lần 2: cùng state, giờ có block trong D1/D2
    report2 = run(TODAY, [source], reader2, writer2, PRESET_STUDENT_VN, dry_run=False, now=NOW)
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
    report1 = run(TODAY, [source], reader, writer1, PRESET_STUDENT_VN, dry_run=False, now=NOW)
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
    report2 = run(TODAY, [source2], reader2, writer2, PRESET_STUDENT_VN, dry_run=False, now=NOW)

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
        now=NOW,
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
        now=NOW,
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
        now=NOW,
    )
    assert [entry.event_id for entry in third.planned_deletes] == ["fake-event-1"]
    assert [entry.event_id for entry in third.deleted if entry.success] == ["fake-event-1"]
    assert third.created == ()
    assert reader_for(writer).list_auto_blocks(
        datetime.combine(d1, time(0, 0), tzinfo=tz),
        datetime.combine(d2 + timedelta(days=1), time(0, 0), tzinfo=tz),
    ) == ()


# ---------------------------------------------------------------- nối llm (2B)


# Các test được thêm ở phần nối LLM này — không nằm trong "test cũ".
_NEW_LLM_TESTS = (
    "test_runner_llm_none_all_previous_tests_still_pass",
    "test_runner_passes_llm_through_to_parse_tasks",
    "test_runner_llm_failure_does_not_crash_run",
    "test_runner_report_shows_estimate_source",
    "test_runner_store_reaches_parse_title_cache",
    "test_runner_bad_request_returns_report_without_writing",
    "test_runner_real_write_without_store_is_blocked",
    "test_runner_dry_run_without_store_only_warns",
    "test_runner_warns_when_now_missing",
    "test_runner_real_write_without_now_is_blocked",
)


def _legacy_runner_tests():
    """Mọi test có sẵn của file này trước khi nối LLM."""
    import tests.test_runner as mod

    return [
        (name, fn)
        for name, fn in vars(mod).items()
        if name.startswith("test_") and name not in _NEW_LLM_TESTS and callable(fn)
    ]


def test_runner_llm_none_all_previous_tests_still_pass():
    """Không truyền llm → mọi test runner cũ vẫn xanh, và parse_tasks luôn nhận llm=None.

    Bằng chứng LLM không lẻn vào thành phụ thuộc ngầm ở tầng runner.
    """
    import inspect

    import horae.runner as runner_mod
    from horae.adapters.parsing import parse_tasks as real_parse_tasks

    seen: list[tuple[object, object]] = []

    def spy(raw_tasks, config, llm=None, store=None):
        seen.append((llm, store))
        return real_parse_tasks(raw_tasks, config, llm, store)

    with pytest.MonkeyPatch.context() as outer:
        outer.setattr(runner_mod, "parse_tasks", spy)
        legacy = _legacy_runner_tests()
        assert len(legacy) >= 9  # 9 test cũ của file này
        for _name, fn in legacy:
            if "monkeypatch" in inspect.signature(fn).parameters:
                with pytest.MonkeyPatch.context() as inner:
                    fn(inner)
            else:
                fn()

    assert seen  # đã thực sự đi qua parse_tasks
    assert all(llm is None and store is None for llm, store in seen)

    # llm=None tường minh cho kết quả y hệt lúc không truyền tham số
    source_a, reader_a = _basic_setup()
    source_b, reader_b = _basic_setup()
    report_default = run(TODAY, [source_a], reader_a, None, PRESET_STUDENT_VN, dry_run=True)
    report_none = run(
        TODAY, [source_b], reader_b, None, PRESET_STUDENT_VN, llm=None, dry_run=True
    )
    assert report_none.parse == report_default.parse
    assert report_none.plan.blocks == report_default.plan.blocks
    assert report_none.warnings == report_default.warnings
    assert report_none.errors == report_default.errors


def test_runner_passes_llm_through_to_parse_tasks():
    """Truyền llm vào run() → llm THẬT SỰ được dùng, runner không âm thầm bỏ qua."""
    from tests.fakes import FakeLLMClient

    llm = FakeLLMClient(minutes=45, title="Ôn chương điện xoay chiều")
    tasks = [_raw_task("tudo", "Ôn lại chương điện xoay chiều", due=date(2026, 1, 7))]
    source, reader = _basic_setup(tasks=tasks)
    report = run(
        TODAY, [source], reader, None, PRESET_STUDENT_VN, llm=llm, dry_run=True
    )
    assert llm.call_count > 0
    assert report.estimate_sources["tudo"] == "llm"
    assert report.parse.assignments[0].estimate_minutes == 45


def test_runner_llm_failure_does_not_crash_run():
    """LLM lỗi mọi provider → run() KHÔNG raise, task đó về default, task khác vẫn chạy."""
    from horae.llm.registry import LLMAllProvidersFailed
    from tests.fakes import FakeLLMClient

    llm = FakeLLMClient(error=LLMAllProvidersFailed((("p1", "LLMQuotaError"),)))
    tasks = [
        _raw_task("vatli", "Làm BTVN Vật Lí [180m]", due=date(2026, 1, 7)),
        _raw_task("tudo", "Ôn lại chương điện xoay chiều", due=date(2026, 1, 7)),
        _raw_task("ielts", "Ôn IELTS [60m/ngày]", labels=("ontap",)),
    ]
    source, reader = _basic_setup(tasks=tasks)
    report = run(
        TODAY, [source], reader, None, PRESET_STUDENT_VN, llm=llm, dry_run=True
    )
    assert llm.call_count == 1
    sources = report.estimate_sources
    assert sources["tudo"] == "default"
    assert sources["vatli"] == "title"        # task khác vẫn xử lý bình thường
    assert sources["ielts"] == "title"
    by_id = {a.task_id: a for a in report.parse.assignments}
    assert by_id["tudo"].estimate_minutes == 60   # rơi về mặc định
    assert by_id["vatli"].estimate_minutes == 180
    assert any("LLM" in w and "tudo" in w for w in report.warnings)


def test_runner_report_shows_estimate_source():
    """Báo cáo phân biệt được "60 phút vì LLM đoán" và "60 phút vì không đoán được"."""
    from tests.fakes import FakeLLMClient

    llm = FakeLLMClient(minutes=60, title="Ôn chương điện")
    tasks = [
        _raw_task("vatli", "Làm BTVN Vật Lí [180m]", due=date(2026, 1, 7)),
        _raw_task("tudo", "Ôn lại chương điện xoay chiều", due=date(2026, 1, 7)),
        _raw_task("ielts", "Ôn IELTS [60m/ngày]", labels=("ontap",)),
    ]
    source, reader = _basic_setup(tasks=tasks)
    report = run(
        TODAY, [source], reader, None, PRESET_STUDENT_VN, llm=llm, dry_run=True
    )
    table = report.classification_table()
    assert "Todoist — phân loại" in table
    assert "source" in table
    for line in table.splitlines():
        if line.startswith("tudo"):
            assert "llm" in line and "60" in line
        if line.startswith("vatli"):
            assert "title" in line and "180" in line
    assert {row.kind for row in report.classifications} == {"assignment", "ongoing"}


def test_runner_store_reaches_parse_title_cache(tmp_path):
    """store truyền qua run() → lần chạy thứ hai trúng cache, không gọi LLM lại."""
    from horae.state.store import LocalStore
    from tests.fakes import FakeLLMClient

    store = LocalStore(str(tmp_path))
    llm = FakeLLMClient(minutes=45)
    tasks = [_raw_task("tudo", "Ôn lại chương điện xoay chiều", due=date(2026, 1, 7))]

    source, reader = _basic_setup(tasks=tasks)
    first = run(TODAY, [source], reader, None, PRESET_STUDENT_VN,
                llm=llm, store=store, dry_run=True)
    assert llm.call_count == 1

    source2, reader2 = _basic_setup(tasks=tasks)
    second = run(TODAY, [source2], reader2, None, PRESET_STUDENT_VN,
                 llm=llm, store=store, dry_run=True)
    assert llm.call_count == 1  # cache chặn lời gọi thứ hai
    assert second.estimate_sources == first.estimate_sources == {"tudo": "llm"}
    assert second.parse.assignments == first.parse.assignments
    assert not any("KHÔNG được cache" in w for w in first.warnings)


def test_runner_bad_request_returns_report_without_writing():
    """LLMBadRequestError → run() dừng, KHÔNG ghi, nhưng vẫn trả báo cáo có dấu vết."""
    from horae.llm.protocol import LLMBadRequestError
    from tests.fakes import FakeLLMClient

    import tempfile

    from horae.state.store import LocalStore

    llm = FakeLLMClient(error=LLMBadRequestError("prompt sai"))
    writer = FakeCalendarWriter()
    tasks = [
        _raw_task("vatli", "Làm BTVN Vật Lí [180m]", due=date(2026, 1, 7)),
        _raw_task("tudo", "Ôn lại chương điện xoay chiều", due=date(2026, 1, 7)),
    ]
    source, reader = _basic_setup(tasks=tasks)
    # store thật để đi qua được cổng llm_without_store — ca cần kiểm ở đây là
    # prompt sai, không phải thiếu cache.
    with tempfile.TemporaryDirectory() as tmp:
        report = run(TODAY, [source], reader, writer, PRESET_STUDENT_VN,
                     llm=llm, store=LocalStore(tmp), dry_run=False, now=NOW)

    assert len(writer.created) == 0 and len(writer.deleted) == 0  # không chạm calendar
    assert report.plan.ok is False
    assert any(e.startswith("llm_bad_request:") for e in report.errors)
    assert any("prompt LLM sai" in w for w in report.warnings)
    assert report.parse.assignments == ()   # không task nào được xử lý tiếp
    assert report.reconciliation is None


def test_runner_real_write_without_store_is_blocked():
    """dry_run=False + llm + thiếu store → DỪNG, không ghi, không gọi LLM lần nào.

    Ghi thật bằng ước lượng LLM không cache là cấu hình phi xác định: mỗi đêm
    một con số khác cho cùng một task chưa đổi nội dung. Cổng chặn chạy trước
    mọi lời gọi mạng nên cũng không tốn API call nào.
    """
    from tests.fakes import FakeLLMClient

    llm = FakeLLMClient(minutes=45)
    writer = FakeCalendarWriter()
    source, reader = _basic_setup()
    report = run(TODAY, [source], reader, writer, PRESET_STUDENT_VN,
                 llm=llm, store=None, dry_run=False, now=NOW)

    assert report.plan.ok is False
    assert any(e.startswith("llm_without_store:") for e in report.errors)
    assert len(writer.created) == 0 and len(writer.deleted) == 0
    assert report.reconciliation is None
    assert llm.call_count == 0          # dừng trước cả khi đọc task
    assert source.fetch_count == 0      # không tốn lời gọi Todoist nào

    # Có store → không bị chặn
    import tempfile

    from horae.state.store import LocalStore

    with tempfile.TemporaryDirectory() as tmp:
        source2, reader2 = _basic_setup()
        ok_report = run(TODAY, [source2], reader2, FakeCalendarWriter(),
                        PRESET_STUDENT_VN, llm=llm, store=LocalStore(tmp),
                        dry_run=False, now=NOW)
    assert not any(e.startswith("llm_without_store:") for e in ok_report.errors)

    # Không có llm → cổng không áp (đường không-LLM ghi thật vẫn như cũ)
    source3, reader3 = _basic_setup()
    no_llm = run(TODAY, [source3], reader3, FakeCalendarWriter(),
                 PRESET_STUDENT_VN, dry_run=False, now=NOW)
    assert not any(e.startswith("llm_without_store:") for e in no_llm.errors)
    assert no_llm.plan.ok is True


def test_runner_dry_run_without_store_only_warns():
    """dry_run=True + llm + thiếu store → chỉ cảnh báo, vẫn chạy hết (không ghi gì)."""
    from tests.fakes import FakeLLMClient

    llm = FakeLLMClient(minutes=45)
    tasks = [_raw_task("tudo", "Ôn lại chương điện xoay chiều", due=date(2026, 1, 7))]
    source, reader = _basic_setup(tasks=tasks)
    report = run(TODAY, [source], reader, None, PRESET_STUDENT_VN,
                 llm=llm, store=None, dry_run=True)

    assert not any(e.startswith("llm_without_store:") for e in report.errors)
    assert any("KHÔNG được cache" in w for w in report.warnings)
    assert report.estimate_sources["tudo"] == "llm"


def test_runner_warns_when_now_missing():
    """now=None → cảnh báo nêu CẢ HAI hậu quả, không chỉ nhãn hiển thị.

    Hậu quả thứ hai nặng hơn: reconcile chỉ chạy kiểm "block đã bắt đầu thì
    không xóa" khi now khác None.
    """
    source, reader = _basic_setup()
    report = run(TODAY, [source], reader, None, PRESET_STUDENT_VN, dry_run=True)
    warn = [w for w in report.warnings if w.startswith("now=None:")]
    assert len(warn) == 1
    assert "overdue" in warn[0] and "đã bắt đầu" in warn[0]

    # Truyền now → không còn cảnh báo
    source2, reader2 = _basic_setup()
    exact = run(TODAY, [source2], reader2, None, PRESET_STUDENT_VN, dry_run=True,
                now=datetime.combine(TODAY, time(12, 23), tzinfo=TZ))
    assert not any(w.startswith("now=None:") for w in exact.warnings)


def test_runner_real_write_without_now_is_blocked():
    """dry_run=False + thiếu now → DỪNG, không ghi, không gọi mạng.

    Lý do chặn không phải nhãn hiển thị: reconcile chỉ chạy kiểm "replacement
    delete bị chặn do block đã bắt đầu" khi now khác None, nên thiếu now ở chế
    độ ghi là tắt cơ chế chống xóa block đang chạy dở.
    """
    writer = FakeCalendarWriter()
    source, reader = _basic_setup()
    report = run(TODAY, [source], reader, writer, PRESET_STUDENT_VN, dry_run=False)

    assert report.plan.ok is False
    assert any(e.startswith("now_required_for_write:") for e in report.errors)
    assert "đã bắt đầu" in report.errors[0]
    assert len(writer.created) == 0 and len(writer.deleted) == 0
    assert report.reconciliation is None
    assert source.fetch_count == 0   # chặn trước mọi lời gọi mạng

    # Có now → chạy bình thường
    source2, reader2 = _basic_setup()
    ok_report = run(TODAY, [source2], reader2, FakeCalendarWriter(),
                    PRESET_STUDENT_VN, dry_run=False, now=NOW)
    assert not any(e.startswith("now_required_for_write:") for e in ok_report.errors)
    assert ok_report.plan.ok is True

    # dry_run=True thiếu now → KHÔNG chặn, chỉ cảnh báo
    source3, reader3 = _basic_setup()
    dry = run(TODAY, [source3], reader3, None, PRESET_STUDENT_VN, dry_run=True)
    assert not any(e.startswith("now_required_for_write:") for e in dry.errors)
    assert any(w.startswith("now=None:") for w in dry.warnings)
    assert dry.plan.ok is True
