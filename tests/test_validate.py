"""Test cho scheduler_core.validate (run_checks)."""

from dataclasses import replace
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from scheduler_core.config import DEFAULT_CONFIG, PRESET_STUDENT_VN, BreakWindow
from scheduler_core.models import Assignment, Block, DayCapacity, FixedEvent, Interval, OngoingTask
from scheduler_core.validate import run_checks

# Config không break — để capacity_consistent pass khi free_intervals thủ công không trừ break.
NO_BREAK_CONFIG = replace(DEFAULT_CONFIG, breaks=())

MON = date(2026, 1, 5)
TUE = date(2026, 1, 6)
WED = date(2026, 1, 7)
SAT = date(2026, 1, 10)
TZ = ZoneInfo("UTC")


def mk_interval(day, sh, sm, eh, em):
    return Interval(
        datetime.combine(day, time(sh, sm), tzinfo=TZ),
        datetime.combine(day, time(eh, em), tzinfo=TZ),
    )


def mk_capacity(day, intervals, *, ceiling=360, leisure_interval_override=None):
    free = tuple(intervals)
    return DayCapacity(
        date=day,
        free_intervals=free,
        busy_minutes=0,
        ceiling_minutes=ceiling,
        capacity_minutes=sum(iv.duration_minutes for iv in free),
        dropped_fragments=(),
        leisure_interval=leisure_interval_override,
    )


def mk_block(tid, title, day, sh, sm, eh, em, kind="assignment"):
    return Block(
        task_id=tid,
        title=title,
        start=datetime.combine(day, time(sh, sm), tzinfo=TZ),
        end=datetime.combine(day, time(eh, em), tzinfo=TZ),
        kind=kind,
    )


def mk_assignment(tid, deadline_dt, estimate, *, title=None):
    return Assignment(task_id=tid, title=title or tid, estimate_minutes=estimate, deadline=deadline_dt)


def _check(results, name):
    return next(r for r in results if r.name == name)


# ---------------------------------------------------------------- valid


def test_13_valid_schedule_all_pass():
    caps = {
        MON: mk_capacity(MON, [mk_interval(MON, 7, 0, 22, 0)]),
        TUE: mk_capacity(TUE, [mk_interval(TUE, 7, 0, 22, 0)]),
    }
    assignments = [mk_assignment("t1", datetime.combine(TUE, time(21, 0), tzinfo=TZ), 120)]
    ongoing = [OngoingTask("o1", "o1", 60)]
    allocation = {MON: {"t1": 60}, TUE: {"t1": 60}}
    blocks = {
        MON: [mk_block("t1", "t1", MON, 7, 0, 8, 0), mk_block("o1", "o1", MON, 8, 20, 9, 20, "ongoing")],
        TUE: [mk_block("t1", "t1", TUE, 7, 0, 8, 0)],
    }
    results = run_checks(blocks, caps, assignments, ongoing, allocation, NO_BREAK_CONFIG, events_by_day={})
    assert len(results) == 12
    for cr in results:
        assert cr.passed, f"{cr.name}: {cr.detail}"


# ---------------------------------------------------------------- failures


def test_14_block_overlaps_leisure_fails():
    """Block đè leisure (verify tự tính) → no_overlap_with_blocked FAIL.
    Dùng config có leisure thật + event thật để verify tự tính leisure đúng vị trí."""
    from scheduler_core.config import LeisureRule
    cfg_leisure = replace(DEFAULT_CONFIG,
        breaks=(),
        leisure=LeisureRule(minutes=120, min_minutes=60, days=frozenset({0,1,2,3,4,5,6}), placement="latest"),
    )
    # Dùng Saturday có event IELTS 18:00-21:30 (offline)
    SAT = date(2026, 1, 10)
    SAT_TZ = ZoneInfo("UTC")
    event = FixedEvent(
        title="IELTS",
        start=datetime.combine(SAT, time(18, 0), tzinfo=SAT_TZ),
        end=datetime.combine(SAT, time(21, 30), tzinfo=SAT_TZ),
        requires_travel=True,
    )
    # verify tự tính: free trước leisure = 07:00-18:00 (trừ event + break lunch)
    # find_contiguous_block(07:00-18:00, 120, "latest") → 16:00-18:00
    # Vậy leisure đúng = 16:00-18:00
    # Block 17:00-18:30 đè leisure 16:00-18:00 → no_overlap FAIL
    leisure_for_test = None  # để verify tự tính
    caps = {SAT: mk_capacity(SAT, [mk_interval(SAT, 7, 0, 12, 0), mk_interval(SAT, 18, 0, 22, 0)],
             leisure_interval_override=leisure_for_test)}
    assignments = [mk_assignment("t1", datetime.combine(SAT, time(21, 0), tzinfo=SAT_TZ), 60)]
    allocation = {SAT: {"t1": 60}}
    blocks = {SAT: [mk_block("t1", "t1", SAT, 17, 0, 18, 30)]}
    results = run_checks(blocks, caps, assignments, [], allocation, cfg_leisure,
                        events_by_day={SAT: [event]})
    cr = _check(results, "no_overlap_with_blocked")
    assert not cr.passed
    assert "t1" in cr.detail


def test_15_overlapping_blocks_fail():
    caps = {MON: mk_capacity(MON, [mk_interval(MON, 7, 0, 22, 0)])}
    assignments = [mk_assignment("t1", datetime.combine(MON, time(21, 0), tzinfo=TZ), 60)]
    allocation = {MON: {"t1": 60}}
    blocks = {MON: [mk_block("t1", "t1", MON, 7, 0, 8, 0), mk_block("t2", "t2", MON, 7, 30, 8, 30)]}
    results = run_checks(blocks, caps, assignments, [], allocation, NO_BREAK_CONFIG, events_by_day={})
    cr = _check(results, "no_block_overlap")
    assert not cr.passed


def test_16_block_too_small_fail():
    caps = {MON: mk_capacity(MON, [mk_interval(MON, 7, 0, 22, 0)])}
    assignments = [mk_assignment("t1", datetime.combine(MON, time(21, 0), tzinfo=TZ), 25)]
    allocation = {MON: {"t1": 25}}
    blocks = {MON: [mk_block("t1", "t1", MON, 7, 0, 7, 25)]}
    results = run_checks(blocks, caps, assignments, [], allocation, NO_BREAK_CONFIG, events_by_day={})
    cr = _check(results, "block_size")
    assert not cr.passed
    assert "25" in cr.detail


def test_17_day_total_exceeds_ceiling_fail():
    caps = {MON: mk_capacity(MON, [mk_interval(MON, 7, 0, 22, 0)], ceiling=120)}
    assignments = [
        mk_assignment("t1", datetime.combine(MON, time(21, 0), tzinfo=TZ), 90),
        mk_assignment("t2", datetime.combine(MON, time(21, 0), tzinfo=TZ), 90),
    ]
    allocation = {MON: {"t1": 90, "t2": 90}}
    blocks = {MON: [mk_block("t1", "t1", MON, 7, 0, 8, 30), mk_block("t2", "t2", MON, 8, 50, 10, 20)]}
    results = run_checks(blocks, caps, assignments, [], allocation, NO_BREAK_CONFIG, events_by_day={})
    cr = _check(results, "within_ceiling")
    assert not cr.passed
    assert "180" in cr.detail or "ceiling" in cr.detail


def test_18_block_after_deadline_fail():
    deadline = datetime.combine(MON, time(8, 0), tzinfo=TZ)
    caps = {MON: mk_capacity(MON, [mk_interval(MON, 7, 0, 22, 0)])}
    assignments = [mk_assignment("t1", deadline, 60)]
    allocation = {MON: {"t1": 60}}
    blocks = {MON: [mk_block("t1", "t1", MON, 7, 30, 8, 30)]}  # kết thúc 08:30 > deadline 08:00
    results = run_checks(blocks, caps, assignments, [], allocation, NO_BREAK_CONFIG, events_by_day={})
    cr = _check(results, "before_deadline")
    assert not cr.passed
    assert "t1" in cr.detail


def test_19_day_dominance_fail():
    # remaining 200 > threshold 90 → check no_day_dominance
    # MON nhận 120 > 100 (50% của 200)
    caps = {
        MON: mk_capacity(MON, [mk_interval(MON, 7, 0, 22, 0)]),
        TUE: mk_capacity(TUE, [mk_interval(TUE, 7, 0, 22, 0)]),
    }
    assignments = [mk_assignment("t1", datetime.combine(TUE, time(21, 0), tzinfo=TZ), 200)]
    allocation = {MON: {"t1": 120}, TUE: {"t1": 80}}
    blocks = {
        MON: [mk_block("t1", "t1", MON, 7, 0, 9, 0)],
        TUE: [mk_block("t1", "t1", TUE, 7, 0, 8, 20)],
    }
    results = run_checks(blocks, caps, assignments, [], allocation, NO_BREAK_CONFIG, events_by_day={})
    cr = _check(results, "no_day_dominance")
    assert not cr.passed
    assert "120" in cr.detail


def test_20_branch_b_on_deadline_date_fail():
    # remaining 60 <= 90, days_until = (WED - MON).days = 2 <= 7 → branch B
    # block trên chính ngày deadline (WED) → fail
    caps = {
        MON: mk_capacity(MON, [mk_interval(MON, 7, 0, 22, 0)]),
        TUE: mk_capacity(TUE, [mk_interval(TUE, 7, 0, 22, 0)]),
        WED: mk_capacity(WED, [mk_interval(WED, 7, 0, 22, 0)]),
    }
    deadline = datetime.combine(WED, time(21, 0), tzinfo=TZ)
    assignments = [mk_assignment("t1", deadline, 60)]
    allocation = {WED: {"t1": 60}}
    blocks = {WED: [mk_block("t1", "t1", WED, 7, 0, 8, 0)]}
    results = run_checks(blocks, caps, assignments, [], allocation, NO_BREAK_CONFIG, events_by_day={})
    cr = _check(results, "small_task_buffer")
    assert not cr.passed
    assert "t1" in cr.detail


def test_21_run_checks_never_raises():
    # Truyền data vỡ nát → không raise, vẫn trả list[CheckResult]
    results = run_checks(
        {MON: [mk_block("bad", "bad", MON, 6, 0, 23, 59)]},
        {MON: mk_capacity(MON, [mk_interval(MON, 7, 0, 22, 0)], ceiling=10)},
        [mk_assignment("bad", datetime.combine(MON, time(6, 0), tzinfo=TZ), 999)],
        [],
        {MON: {"bad": 999}},
        NO_BREAK_CONFIG,
        events_by_day={},
    )
    assert isinstance(results, list)
    assert all(hasattr(r, "passed") for r in results)


# ---------------------------------------------------------------- severity


def test_26_day_dominance_warning_not_error():
    """Lịch dồn 105/75 trên task 180' → no_day_dominance fail nhưng severity=warning."""
    caps = {
        MON: mk_capacity(MON, [mk_interval(MON, 7, 0, 22, 0)]),
        TUE: mk_capacity(TUE, [mk_interval(TUE, 7, 0, 22, 0)]),
    }
    assignments = [mk_assignment("t1", datetime.combine(TUE, time(21, 0), tzinfo=TZ), 180)]
    allocation = {MON: {"t1": 105}, TUE: {"t1": 75}}
    blocks = {
        MON: [mk_block("t1", "t1", MON, 7, 0, 8, 45)],
        TUE: [mk_block("t1", "t1", TUE, 7, 0, 8, 15)],
    }
    results = run_checks(blocks, caps, assignments, [], allocation, NO_BREAK_CONFIG, events_by_day={})
    dominance = _check(results, "no_day_dominance")
    assert not dominance.passed
    assert dominance.severity == "warning"


def test_27_block_overlap_is_error():
    caps = {MON: mk_capacity(MON, [mk_interval(MON, 7, 0, 22, 0)])}
    assignments = [mk_assignment("t1", datetime.combine(MON, time(21, 0), tzinfo=TZ), 60)]
    allocation = {MON: {"t1": 60}}
    blocks = {MON: [mk_block("t1", "t1", MON, 7, 0, 8, 0), mk_block("t2", "t2", MON, 7, 30, 8, 30)]}
    results = run_checks(blocks, caps, assignments, [], allocation, NO_BREAK_CONFIG, events_by_day={})
    overlap = _check(results, "no_block_overlap")
    assert not overlap.passed
    assert overlap.severity == "error"


def test_28_all_pass_no_warnings():
    caps = {
        MON: mk_capacity(MON, [mk_interval(MON, 7, 0, 22, 0)]),
        TUE: mk_capacity(TUE, [mk_interval(TUE, 7, 0, 22, 0)]),
    }
    assignments = [mk_assignment("t1", datetime.combine(TUE, time(21, 0), tzinfo=TZ), 120)]
    ongoing = [OngoingTask("o1", "o1", 60)]
    allocation = {MON: {"t1": 60}, TUE: {"t1": 60}}
    blocks = {
        MON: [mk_block("t1", "t1", MON, 7, 0, 8, 0), mk_block("o1", "o1", MON, 8, 20, 9, 20, "ongoing")],
        TUE: [mk_block("t1", "t1", TUE, 7, 0, 8, 0)],
    }
    results = run_checks(blocks, caps, assignments, ongoing, allocation, NO_BREAK_CONFIG, events_by_day={})
    for cr in results:
        assert cr.passed, f"{cr.name}: {cr.detail}"


def test_29_integration_planning_horizon_2_ok_with_warning():
    """Integration test 19: planning_horizon=2 → ok is True, warnings chứa 'no_day_dominance'."""
    from scheduler_core.plan import build_plan
    from tests.fixtures import assignments, events_by_day, ongoing, TODAY

    cfg2 = replace(PRESET_STUDENT_VN, planning_horizon_days=2)
    result = build_plan(TODAY, assignments(), ongoing(), events_by_day(), {}, cfg2)
    assert result.ok is True
    assert "no_day_dominance" in result.warnings
