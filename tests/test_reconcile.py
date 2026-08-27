"""Task 3B reconciliation tests.  All calendar state is in-memory."""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from horae.adapters.protocols import AutoBlock
from horae.reconcile import reconcile
from scheduler_core.models import Block, CheckResult
from scheduler_core.plan import PlanResult
from tests.fakes import FakeCalendarWriter


TZ = ZoneInfo("Asia/Ho_Chi_Minh")
D1 = date(2026, 1, 5)
D2 = date(2026, 1, 6)
D0 = D1 - timedelta(days=1)


def _block(task_id, day, sh, minutes=30, *, kind="assignment"):
    start = datetime.combine(day, time(sh, 0), tzinfo=TZ)
    return Block(
        task_id=task_id,
        title=task_id,
        start=start,
        end=start + timedelta(minutes=minutes),
        kind=kind,
    )


def _auto(event_id, task_id, day, sh, minutes=30, *, title=None, description=None,
          calendar_id="auto-study", calendar_name="Auto-Study"):
    start = datetime.combine(day, time(sh, 0), tzinfo=TZ)
    return AutoBlock(
        event_id=event_id,
        task_id=task_id,
        title=title or f"[Auto] [assignment] {task_id}",
        start=start,
        end=start + timedelta(minutes=minutes),
        kind="assignment",
        calendar_id=calendar_id,
        description=description if description is not None else f"Todoist task ID: {task_id}",
        calendar_name=calendar_name,
    )


def _plan(blocks, *, ok=True):
    return PlanResult(
        blocks=list(blocks),
        projected={},
        capacities={},
        checks=[CheckResult("fake", ok, "fake")],
        ok=ok,
        warnings=(),
        completed_task_ids=(),
        infeasible_task_ids=(),
        shortfall={},
    )


def _run(plan, existing, task_ids, *, writer=None, dry_run=False, **kwargs):
    return reconcile(
        plan,
        existing,
        task_ids,
        D1,
        D2,
        writer=writer,
        dry_run=dry_run,
        **kwargs,
    )


# ---------------------------------------------------------------- diff 12-17


def test_12_equal_totals_do_nothing():
    existing = [_auto("old", "t1", D1, 7, 60)]
    writer = FakeCalendarWriter(existing_blocks=existing)
    report = _run(_plan([_block("t1", D1, 7), _block("t1", D1, 8)]), existing, {"t1"}, writer=writer)

    assert report.gate_passed
    assert report.planned_deletes == ()
    assert report.planned_creates == ()
    assert writer.deleted == []
    assert writer.created == []
    assert report.recovered == ()


def test_13_existing_less_than_planned_creates_only_deficit():
    existing = [_auto("old", "t1", D1, 7)]
    writer = FakeCalendarWriter(existing_blocks=existing)
    report = _run(
        _plan([_block("t1", D1, 7), _block("t1", D1, 8), _block("t1", D1, 9)]),
        existing,
        {"t1"},
        writer=writer,
    )

    assert [block.start.hour for block in report.planned_creates] == [8, 9]
    assert report.planned_deletes == ()
    assert sum(block.end and (block.end - block.start).seconds // 60 for block, _, _ in writer.created) == 60
    assert not report.mismatch
    assert len(report.recovered) == 2
    assert any("phục hồi ghi dở" in warning for warning in report.warnings)


def test_14_surplus_deletes_latest_and_retains_earliest():
    existing = [
        _auto("e1", "t1", D1, 7),
        _auto("e2", "t1", D1, 8),
        _auto("e3", "t1", D1, 9),
    ]
    writer = FakeCalendarWriter(existing_blocks=existing)
    report = _run(_plan([_block("t1", D1, 7)]), existing, {"t1"}, writer=writer)

    assert [block.event_id for block in report.planned_deletes] == ["e3", "e2"]
    assert writer.deleted == ["e3", "e2"]
    assert [block.event_id for block in writer.read_back(*_range(D1, D2))] == ["e1"]
    assert any("lịch cũ dư" in warning for warning in report.warnings)
    assert not report.mismatch


def test_15_planned_zero_deletes_all_blocks_for_task_day():
    existing = [_auto("e1", "t1", D1, 7), _auto("e2", "t1", D1, 8)]
    writer = FakeCalendarWriter(existing_blocks=existing)
    report = _run(_plan([]), existing, {"t1"}, writer=writer)

    assert report.planned_delete_ids == ("e2", "e1")
    assert writer.deleted == ["e2", "e1"]
    assert not report.mismatch


def test_16_task_missing_from_todoist_is_deletion_candidate():
    existing = [_auto("old", "removed", D1, 7)]
    writer = FakeCalendarWriter(existing_blocks=existing)
    report = _run(_plan([]), existing, {"still-open"}, writer=writer)

    assert report.planned_delete_ids == ("old",)
    assert any("không còn trong Todoist" in warning for warning in report.warnings)
    assert writer.deleted == ["old"]


def test_17_diff_is_independent_for_each_task_and_date():
    existing = [
        _auto("d1-old-1", "t1", D1, 7, 30),
        _auto("d1-old-2", "t1", D1, 8, 30),
        _auto("d2-old", "t2", D2, 7, 30),
    ]
    planned = [_block("t1", D1, 7, 30), _block("t2", D2, 7, 60)]
    writer = FakeCalendarWriter(existing_blocks=existing)
    report = _run(_plan(planned), existing, {"t1", "t2"}, writer=writer)

    assert report.planned_delete_ids == ("d1-old-2",)
    assert [(b.task_id, b.start.date()) for b in report.planned_creates] == [("t2", D2)]
    assert writer.deleted == ["d1-old-2"]
    assert len(writer.created) == 1
    assert not report.mismatch


def test_17a_non_exact_surplus_replaces_latest_block_with_deficit():
    existing = [_auto("old", "t1", D1, 7, 60)]
    writer = FakeCalendarWriter(existing_blocks=existing)
    report = _run(
        _plan([_block("t1", D1, 7, 30)]),
        existing,
        {"t1"},
        writer=writer,
    )

    assert report.gate_delete
    assert report.gate_create
    assert report.planned_delete_ids == ("old",)
    assert [(block.start.hour, block.duration_minutes) for block in report.planned_creates] == [
        (7, 30)
    ]
    assert writer.deleted == ["old"]
    assert len(writer.created) == 1
    assert writer.created[0][0].duration_minutes == 30
    assert not report.mismatch


def test_17b_started_replacement_skips_delete_but_runs_unrelated_create():
    existing = [_auto("old", "t1", D1, 7, 60)]
    writer = FakeCalendarWriter(existing_blocks=existing)
    report = _run(
        _plan([
            _block("t1", D1, 7, 30),
            _block("t2", D1, 10, 30),
        ]),
        existing,
        {"t1", "t2"},
        writer=writer,
        now=datetime.combine(D1, time(7, 0), tzinfo=TZ),
    )

    assert not report.gate_delete
    assert report.gate_create
    assert writer.deleted == []
    assert [(block.task_id, block.duration_minutes) for block, _, _ in writer.created] == [
        ("t2", 30)
    ]
    assert any("replacement delete" in warning and "đã bắt đầu" in warning for warning in report.warnings)
    assert any("delete gate" in warning for warning in report.warnings)


# ---------------------------------------------------------------- safety 18-23


def test_18_plan_not_ok_blocks_all_side_effects():
    existing = [_auto("old", "t1", D1, 7, 60)]
    writer = FakeCalendarWriter(existing_blocks=existing)
    report = _run(_plan([_block("t1", D1, 7)], ok=False), existing, {"t1"}, writer=writer)

    assert not report.gate_passed
    assert writer.deleted == []
    assert writer.created == []
    assert any("PlanResult.ok=False" in error for error in report.errors)


def test_19_wrong_target_calendar_blocks_all_side_effects():
    existing = [_auto("old", "t1", D1, 7, 60)]
    writer = FakeCalendarWriter(existing_blocks=existing)
    writer.target_calendar_name = "Personal"
    report = _run(_plan([]), existing, {"t1"}, writer=writer)

    assert not report.gate_passed
    assert writer.deleted == []
    assert any("target calendar" in error for error in report.errors)


def test_20_non_auto_event_is_never_deleted():
    existing = [_auto("user", "t1", D1, 7, 60, title="Study")]
    writer = FakeCalendarWriter(existing_blocks=existing)
    report = _run(_plan([]), existing, {"t1"}, writer=writer)

    assert not report.gate_passed
    assert writer.deleted == []
    assert any("không bắt đầu" in error for error in report.errors)


def test_21_unparseable_task_description_is_never_deleted():
    existing = [_auto("bad", "t1", D1, 7, 60, description="manual note")]
    writer = FakeCalendarWriter(existing_blocks=existing)
    report = _run(_plan([]), existing, {"t1"}, writer=writer)

    assert not report.gate_passed
    assert writer.deleted == []
    assert any("parseable" in error for error in report.errors)


def test_22_past_block_is_outside_reconciliation_window():
    existing = [_auto("past", "t1", D0, 7, 60)]
    writer = FakeCalendarWriter(existing_blocks=existing)
    report = _run(_plan([]), existing, {"t1"}, writer=writer)

    assert report.gate_passed
    assert report.planned_deletes == ()
    assert writer.deleted == []


def test_23_deletion_threshold_is_max_three_or_half_of_auto_blocks():
    existing = [_auto(f"e{i}", "stale", D1, 7 + i) for i in range(10)]
    writer = FakeCalendarWriter(existing_blocks=existing)
    report = _run(_plan([]), existing, set(), writer=writer)

    assert report.total_auto_blocks == 10
    assert report.deletion_limit == 5
    assert len(report.planned_deletes) == 10
    assert not report.gate_passed
    assert writer.deleted == []
    assert any("vượt ngưỡng" in error for error in report.errors)


# ---------------------------------------------------------------- lifecycle 24-28


def _range(start_day, end_day):
    return (
        datetime.combine(start_day, time.min, tzinfo=TZ),
        datetime.combine(end_day + timedelta(days=1), time.min, tzinfo=TZ),
    )


def test_24_dry_run_reports_exact_actions_without_side_effects():
    existing = [_auto("e1", "t1", D1, 7), _auto("e2", "t1", D1, 8)]
    writer = FakeCalendarWriter(existing_blocks=existing)
    report = _run(_plan([_block("t2", D1, 10)]), existing, {"t2"}, writer=writer, dry_run=True)

    assert report.gate_passed
    assert report.planned_delete_ids == ("e2", "e1")
    assert [(b.task_id, b.start.hour) for b in report.planned_creates] == [("t2", 10)]
    assert report.deleted == ()
    assert report.created == ()
    assert writer.deleted == []
    assert writer.created == []


def test_25_deletes_finish_before_creates_start():
    class OrderedWriter(FakeCalendarWriter):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.calls = []

        def delete_event(self, event_id, calendar_id):
            self.calls.append(("delete", event_id))
            return super().delete_event(event_id, calendar_id)

        def create_block(self, block, meta):
            self.calls.append(("create", block.task_id))
            return super().create_block(block, meta)

    existing = [_auto("e1", "t1", D1, 7, 60)]
    writer = OrderedWriter(existing_blocks=existing)
    report = _run(_plan([_block("t2", D1, 9, 30)]), existing, {"t2"}, writer=writer)

    assert report.gate_passed
    assert [kind for kind, _ in writer.calls] == ["delete", "create"]


def test_26_delete_error_is_reported_and_later_operations_continue():
    existing = [
        _auto("e1", "t1", D1, 7),
        _auto("e2", "t1", D1, 8),
        _auto("e3", "t1", D1, 9),
    ]
    writer = FakeCalendarWriter(existing_blocks=existing, delete_fail_on=0)
    report = _run(_plan([_block("t1", D1, 7), _block("t2", D1, 11)]), existing, {"t1", "t2"}, writer=writer)

    assert report.gate_passed
    assert len(report.deleted) == 2
    assert report.deleted[0].success is False
    assert report.deleted[1].success is True
    assert len(report.created) == 1
    assert any("delete event" in error for error in report.errors)


def test_27_create_error_is_reported_and_later_creates_continue():
    planned = [_block(f"t{i}", D1, 7 + i) for i in range(3)]
    writer = FakeCalendarWriter(fail_on=0)
    report = _run(_plan(planned), [], {f"t{i}" for i in range(3)}, writer=writer)

    assert report.gate_passed
    assert len(report.created) == 3
    assert report.created[0].error is not None
    assert report.created[1].error is None
    assert report.created[2].error is None
    assert len(writer.created) == 2
    assert any("create block" in error for error in report.errors)
    assert report.mismatch


def test_28_rerun_with_surviving_state_finishes_partial_delete_and_create():
    existing = [
        _auto("old1", "t1", D1, 7),
        _auto("old2", "t1", D1, 8),
        _auto("old3", "t1", D1, 9),
    ]
    planned = [_block("t1", D1, 7), _block("t2", D1, 11), _block("t2", D1, 12), _block("t2", D1, 13)]
    writer = FakeCalendarWriter(existing_blocks=existing, delete_fail_on=1, fail_on=1)

    first = _run(_plan(planned), existing, {"t1", "t2"}, writer=writer)
    surviving = writer.read_back(*_range(D1, D2))
    second = _run(_plan(planned), surviving, {"t1", "t2"}, writer=writer)

    assert first.gate_passed
    assert first.deleted[0].success is True
    assert first.deleted[1].success is False
    assert len(first.created) == 3
    assert len(second.planned_deletes) == 1
    assert len(second.planned_creates) == 1
    assert second.deleted[0].success is True
    assert second.created[0].error is None
    assert not second.mismatch


# ---------------------------------------------------------------- sabotage S1-S6


def test_s1_wrong_calendar_id_blocks_delete():
    existing = [_auto("wrong-calendar", "t1", D1, 7, calendar_id="personal")]
    writer = FakeCalendarWriter(existing_blocks=existing)

    report = _run(_plan([]), existing, {"t1"}, writer=writer)

    assert not report.gate_passed
    assert writer.deleted == []
    assert any("calendar_id" in error for error in report.errors)


def test_s2_outside_window_delete_candidate_blocks_delete(monkeypatch):
    outside_window = _auto("too-old", "t1", D1 - timedelta(days=2), 7)
    existing = [outside_window, _auto("in-window", "t1", D1, 8)]
    writer = FakeCalendarWriter(existing_blocks=existing)

    # Simulate a bad diff producing an out-of-window candidate.  The gate must
    # still reject it even though normal diffing filters old calendar state.
    import horae.reconcile as reconcile_module

    monkeypatch.setattr(reconcile_module, "_sort_latest", lambda _blocks: [outside_window])

    report = _run(_plan([]), existing, {"t1"}, writer=writer)

    assert not report.gate_passed
    assert report.planned_delete_ids == ("too-old",)
    assert writer.deleted == []
    assert any("D1/D2" in error for error in report.errors)


def test_s3_missing_auto_prefix_blocks_delete():
    existing = [_auto("manual-title", "t1", D1, 7, title="Study block")]
    writer = FakeCalendarWriter(existing_blocks=existing)

    report = _run(_plan([]), existing, {"t1"}, writer=writer)

    assert not report.gate_passed
    assert writer.deleted == []
    assert any("không bắt đầu" in error for error in report.errors)


def test_s4_deletion_limit_blocks_empty_plan_from_deleting_ten_auto_blocks():
    existing = [_auto(f"old-{index}", "stale", D1, 7 + index) for index in range(10)]
    writer = FakeCalendarWriter(existing_blocks=existing)

    report = _run(_plan([]), existing, set(), writer=writer)

    assert not report.gate_passed
    assert len(report.planned_deletes) == 10
    assert report.deletion_limit == 5
    assert writer.deleted == []
    details = " ".join((*report.errors, *report.warnings))
    assert "50%" in details
    assert "vượt ngưỡng" in details


def test_s5_duplicate_delete_event_id_blocks_all_deletes():
    existing = [
        _auto("duplicate", "t1", D1, 7),
        _auto("duplicate", "t1", D1, 8),
    ]
    writer = FakeCalendarWriter(existing_blocks=existing)

    report = _run(_plan([]), existing, {"t1"}, writer=writer)

    assert not report.gate_passed
    assert report.planned_delete_ids == ("duplicate", "duplicate")
    assert writer.deleted == []
    assert any("trùng event_id duplicate" in error for error in report.errors)


def test_s6_delete_404_is_reported_and_later_deletes_are_attempted():
    existing = [
        _auto("e1", "t1", D1, 7),
        _auto("e2", "t1", D1, 8),
        _auto("missing", "t1", D1, 9),
    ]

    class NotFoundWriter(FakeCalendarWriter):
        def __init__(self):
            super().__init__(existing_blocks=existing)
            self.attempted = []

        def delete_event(self, event_id, calendar_id):
            self.attempted.append(event_id)
            if event_id == "missing":
                raise RuntimeError("404 Not Found")
            return super().delete_event(event_id, calendar_id)

    writer = NotFoundWriter()
    report = _run(_plan([]), existing, {"t1"}, writer=writer)

    assert report.gate_passed
    assert writer.attempted == ["missing", "e2", "e1"]
    assert writer.deleted == ["e2", "e1"]
    assert report.deleted[0].event_id == "missing"
    assert report.deleted[0].success is False
    assert report.deleted[1].success is True
    assert report.deleted[2].success is True
    assert any("404 Not Found" in error for error in report.errors)
