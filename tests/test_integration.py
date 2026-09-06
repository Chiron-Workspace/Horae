"""Test tích hợp build_plan — Part 1E. Dùng PRESET_STUDENT_VN trừ khi test nói khác."""

from dataclasses import replace
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from scheduler_core.config import DEFAULT_CONFIG, PRESET_STUDENT_VN
from scheduler_core.models import Assignment
from scheduler_core.plan import build_plan
from tests.fixtures import (
    TZ,
    FRI,
    SAT,
    SUN,
    THU,
    TODAY,
    TUE,
    WED,
    WEEK_MON,
    assignments,
    events_by_day,
    ongoing,
)

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _day_name(d):
    return DAYS[d.weekday()]


def _task_total(result, tid):
    blocks = sum(b.duration_minutes for b in result.blocks if b.task_id == tid)
    projected = sum(da.get(tid, 0) for da in result.projected.values())
    return blocks + projected


def _blocks_on(result, day):
    return [b for b in result.blocks if b.start.date() == day]


def _check(result, name):
    return next(r for r in result.checks if r.name == name)


def _base_plan(**kw):
    return build_plan(
        TODAY, assignments(), ongoing(), events_by_day(), {}, PRESET_STUDENT_VN, **kw
    )


# ================================================================ Kết quả đúng


def test_1_ok_and_all_checks_pass():
    result = _base_plan()
    assert result.ok is True
    assert len(result.checks) == 12
    for cr in result.checks:
        assert cr.passed, f"{cr.name}: {cr.detail}"


def test_2_specific_values():
    result = _base_plan()
    # Capacities
    assert result.capacities[WEEK_MON].capacity_minutes == 360
    assert result.capacities[TUE].capacity_minutes == 240
    assert result.capacities[WED].capacity_minutes == 240
    assert result.capacities[THU].capacity_minutes == 360
    assert result.capacities[FRI].capacity_minutes == 360
    # Blocks: Monday 4, Tuesday 3
    mon = _blocks_on(result, WEEK_MON)
    tue = _blocks_on(result, TUE)
    assert len(mon) == 4
    assert len(tue) == 3
    # Monday blocks: assignment vatli 07:00–08:15, assignment hoa 09:00–10:00,
    # ongoing ielts 10:30–11:30, ongoing sat 14:00–15:00
    assert mon[0].task_id == "btvn_vatli"
    assert mon[0].start.strftime("%H:%M") == "07:00"
    assert mon[0].end.strftime("%H:%M") == "08:15"
    assert mon[1].task_id == "btvn_hoa"
    assert mon[1].start.strftime("%H:%M") == "09:00"
    assert mon[2].task_id == "on_ielts"
    assert mon[2].start.strftime("%H:%M") == "10:30"
    assert mon[3].task_id == "on_sat"
    assert mon[3].start.strftime("%H:%M") == "14:00"
    # Tuesday: vatli 07:00–08:00, ielts 08:30–09:30, sat 10:00–11:00
    assert tue[0].task_id == "btvn_vatli"
    assert tue[1].task_id == "on_ielts"
    assert tue[2].task_id == "on_sat"
    # Projected: Wed, Thu, Fri
    assert WED in result.projected
    assert THU in result.projected
    assert FRI in result.projected
    assert result.projected[WED]["btvn_vatli"] == 45


def test_3_tuesday_chain_only_morning():
    result = _base_plan()
    tue = _blocks_on(result, TUE)
    # Free chỉ còn 07:00–12:00 → không block nào sau 12:00
    for b in tue:
        assert b.end.hour < 12 or (b.end.hour == 12 and b.end.minute == 0), (
            f"Block {b.title} kết thúc {b.end:%H:%M} sau 12:00"
        )


def test_4_wednesday_busy_sat_clipped():
    result = _base_plan()
    # busy tính SAT 21:30–22:45 chỉ tới 22:00 → busy = 270 (150+120)
    assert result.capacities[WED].busy_minutes == 270


def test_5_monday_overlapping_events_counted_once():
    result = _base_plan()
    # Hai event chồng lấn 20:00–21:00 & 20:00–21:30 → chỉ tính 90' + Brightchamps 30' = 120
    assert result.capacities[WEEK_MON].busy_minutes == 120


def test_6_assignment_totals():
    result = _base_plan()
    assert _task_total(result, "btvn_vatli") == 180
    assert _task_total(result, "btvn_hoa") == 60


def test_7_ongoing_after_assignment_and_only_with_room():
    result = _base_plan()
    for day in [WEEK_MON, TUE]:
        day_blocks = _blocks_on(result, day)
        # Tất cả assignment đứng trước ongoing
        assign_indices = [i for i, b in enumerate(day_blocks) if b.kind == "assignment"]
        ongoing_indices = [i for i, b in enumerate(day_blocks) if b.kind == "ongoing"]
        if assign_indices and ongoing_indices:
            assert max(assign_indices) < min(ongoing_indices), (
                f"{_day_name(day)}: ongoing không đứng sau assignment"
            )
    # Ongoing chỉ xuất hiện ở ngày có dư chỗ (tất cả ngày trong plan đều có ongoing ở đây)
    assert all(
        any(b.kind == "ongoing" for b in _blocks_on(result, d))
        for d in [WEEK_MON, TUE]
    )


# ================================================================ Sổ tiến độ


def test_8_partial_done_remaining_90():
    result = build_plan(
        TODAY, assignments(vatli_done=90), ongoing(), events_by_day(), {}, PRESET_STUDENT_VN
    )
    assert _task_total(result, "btvn_vatli") == 90
    assert result.ok is True


def test_9_done_equals_estimate_completed():
    result = build_plan(
        TODAY, assignments(vatli_done=180), ongoing(), events_by_day(), {}, PRESET_STUDENT_VN
    )
    assert "btvn_vatli" in result.completed_task_ids
    assert sum(b.duration_minutes for b in result.blocks if b.task_id == "btvn_vatli") == 0


def test_10_done_more_than_estimate_clamped():
    result = build_plan(
        TODAY, assignments(vatli_done=200), ongoing(), events_by_day(), {}, PRESET_STUDENT_VN
    )
    assert "btvn_vatli" in result.completed_task_ids
    assert result.ok is True  # không raise


# ================================================================ Không khả thi


def test_11_infeasible_task_no_raise():
    big = Assignment(
        task_id="big",
        title="Big Task",
        estimate_minutes=3000,
        deadline=datetime.combine(WED, time(21, 0), tzinfo=TZ),
    )
    result = build_plan(TODAY, [big], ongoing(), events_by_day(), {}, PRESET_STUDENT_VN)
    assert "big" in result.infeasible_task_ids
    # ok phản ánh đúng trạng thái checks (không raise)
    assert isinstance(result.ok, bool)


def test_12_no_assignments_dend_at_write_end():
    result = build_plan(TODAY, [], ongoing(), events_by_day(), {}, PRESET_STUDENT_VN)
    # dend = cuối write window = Tue
    assert sorted(result.capacities.keys()) == [WEEK_MON, TUE]
    # Ongoing vẫn được xếp
    assert len(result.blocks) > 0
    assert all(b.kind == "ongoing" for b in result.blocks)


# ================================================================ Bất biến quan trọng


def test_13_assignment_blocks_identical_with_without_ongoing():
    r_with = build_plan(TODAY, assignments(), ongoing(), events_by_day(), {}, PRESET_STUDENT_VN)
    r_without = build_plan(TODAY, assignments(), [], events_by_day(), {}, PRESET_STUDENT_VN)
    a_with = [(b.task_id, b.start, b.end) for b in r_with.blocks if b.kind == "assignment"]
    a_without = [(b.task_id, b.start, b.end) for b in r_without.blocks if b.kind == "assignment"]
    assert a_with == a_without


def test_14_existing_blocks_reduce_ongoing():
    r_without = build_plan(TODAY, assignments(), ongoing(), events_by_day(), {}, PRESET_STUDENT_VN)
    r_with = build_plan(TODAY, assignments(), ongoing(), events_by_day(),
                        {WEEK_MON: 240}, PRESET_STUDENT_VN)

    def mon_ongoing(r):
        return sum(b.duration_minutes for b in r.blocks
                   if b.kind == "ongoing" and b.start.date() == WEEK_MON)

    assert mon_ongoing(r_without) > 0        # không có existing → có ongoing
    assert mon_ongoing(r_with) == 0          # existing chiếm hết → không còn


def test_15_deterministic_same_input_same_output():
    r1 = _base_plan()
    r2 = _base_plan()
    assert r1 == r2


# ================================================================ Config khác


def test_16_default_config_different_blocks():
    r_preset = _base_plan()
    r_default = build_plan(
        TODAY, assignments(), ongoing(), events_by_day(), {}, DEFAULT_CONFIG
    )
    assert r_default.ok is True
    preset_starts = [(b.task_id, b.start) for b in r_preset.blocks]
    default_starts = [(b.task_id, b.start) for b in r_default.blocks]
    assert preset_starts != default_starts


def test_17_no_leisure_weekend_more_capacity():
    # Dùng today=THU → d1=FRI, write_horizon=4 → Sat trong write window
    sun_task = Assignment(
        task_id="weekend",
        title="Weekend Task",
        estimate_minutes=120,
        deadline=datetime.combine(SUN, time(21, 0), tzinfo=TZ),
    )
    cfg_with = replace(PRESET_STUDENT_VN, write_horizon_days=4)
    cfg_without = replace(PRESET_STUDENT_VN, write_horizon_days=4, leisure=None)
    r_with = build_plan(THU, [sun_task], [], events_by_day(), {}, cfg_with)
    r_without = build_plan(THU, [sun_task], [], events_by_day(), {}, cfg_without)
    sat_cap_with = r_with.capacities[SAT].capacity_minutes
    sat_cap_without = r_without.capacities[SAT].capacity_minutes
    assert sat_cap_with < sat_cap_without
    # Sat có block nhiều phút hơn khi không có leisure
    sat_with = sum(b.duration_minutes for b in r_with.blocks if b.start.date() == SAT)
    sat_without = sum(b.duration_minutes for b in r_without.blocks if b.start.date() == SAT)
    assert sat_without > sat_with


def test_18_write_horizon_3_spreads_blocks():
    cfg3 = replace(PRESET_STUDENT_VN, write_horizon_days=3)
    result = build_plan(TODAY, assignments(), ongoing(), events_by_day(), {}, cfg3)
    block_days = sorted(set(b.start.date() for b in result.blocks))
    # 3 ngày thay vì 2
    assert len(block_days) == 3
    assert WED in block_days  # ngày thứ 3
    # So với default 2 ngày
    r2 = _base_plan()
    assert len(set(b.start.date() for b in r2.blocks)) == 2


def test_19_planning_horizon_2_caps_dend():
    cfg2 = replace(PRESET_STUDENT_VN, planning_horizon_days=2)
    result = build_plan(TODAY, assignments(), ongoing(), events_by_day(), {}, cfg2)
    # dend bị chặn ở Tue (d1 + 1)
    assert sorted(result.capacities.keys()) == [WEEK_MON, TUE]
    # Vật Lí vẫn nhận đủ 180 (dồn vào 2 ngày)
    assert _task_total(result, "btvn_vatli") == 180
    # Phân bổ dồn hơn: Monday nhận nhiều hơn 75' (so với 75' khi 5 ngày)
    mon_vatli = sum(
        b.duration_minutes for b in result.blocks if b.task_id == "btvn_vatli" and b.start.date() == WEEK_MON
    )
    r_default = _base_plan()
    mon_vatli_default = sum(
        b.duration_minutes for b in r_default.blocks if b.task_id == "btvn_vatli" and b.start.date() == WEEK_MON
    )
    assert mon_vatli > mon_vatli_default
    # no_day_dominance là warning, không phải error → ok vẫn True
    assert result.ok is True
    assert "no_day_dominance" in result.warnings


# ================================================================ overdue ≠ infeasible


def _task(tid, minutes, deadline):
    return Assignment(task_id=tid, title=tid, estimate_minutes=minutes, deadline=deadline)


def test_overdue_deadline_past_now_is_not_infeasible():
    """Hạn đã trôi qua tại thời điểm chạy → overdue, KHÔNG phải infeasible.

    Hai nhãn ứng với hai hành động khác nhau: overdue là việc của người dùng
    (dọn Todoist), infeasible là cảnh báo sớm của hệ thống.
    """
    late = _task("late", 60, datetime.combine(TODAY - timedelta(days=7), time(15, 30), tzinfo=TZ))
    now = datetime.combine(TODAY, time(12, 23), tzinfo=TZ)
    result = build_plan(TODAY, [late], ongoing(), events_by_day(), {}, PRESET_STUDENT_VN, now)
    assert result.overdue_task_ids == ("late",)
    assert "late" not in result.infeasible_task_ids


def test_due_today_after_now_is_infeasible_not_overdue():
    """Hạn hôm nay nhưng chưa tới giờ → CHƯA trễ, nhưng lịch chỉ xếp từ D1.

    Đây là ca thường gặp (task nộp trong ngày) và trước đây bị gộp chung vào
    infeasible cùng với task quá hạn thật.
    """
    today_task = _task("today", 60, datetime.combine(TODAY, time(15, 45), tzinfo=TZ))
    now = datetime.combine(TODAY, time(12, 23), tzinfo=TZ)
    result = build_plan(TODAY, [today_task], ongoing(), events_by_day(), {}, PRESET_STUDENT_VN, now)
    assert result.overdue_task_ids == ()
    assert "today" in result.infeasible_task_ids


def test_future_deadline_too_big_is_infeasible_not_overdue():
    """Hạn còn ở tương lai nhưng không đủ chỗ → infeasible, không overdue."""
    big = _task("big", 3000, datetime.combine(WED, time(21, 0), tzinfo=TZ))
    now = datetime.combine(TODAY, time(12, 23), tzinfo=TZ)
    result = build_plan(TODAY, [big], ongoing(), events_by_day(), {}, PRESET_STUDENT_VN, now)
    assert result.overdue_task_ids == ()
    assert "big" in result.infeasible_task_ids


def test_deadline_tomorrow_fits_is_neither():
    """Hạn ngày mai và đủ chỗ → không overdue, không infeasible."""
    ok_task = _task("ok", 60, datetime.combine(WEEK_MON, time(21, 0), tzinfo=TZ))
    now = datetime.combine(TODAY, time(12, 23), tzinfo=TZ)
    result = build_plan(TODAY, [ok_task], ongoing(), events_by_day(), {}, PRESET_STUDENT_VN, now)
    assert result.overdue_task_ids == ()
    assert result.infeasible_task_ids == ()


def test_now_none_defaults_to_start_of_today():
    """now=None → mốc là 00:00 hôm nay; lõi không đọc đồng hồ hệ thống.

    Hệ quả có chủ đích: task hạn sớm hơn trong CHÍNH hôm nay vào infeasible
    chứ không phải overdue, vì lõi không biết bây giờ là mấy giờ.
    """
    yesterday = _task("y", 60, datetime.combine(TODAY - timedelta(days=1), time(21, 0), tzinfo=TZ))
    this_morning = _task("m", 60, datetime.combine(TODAY, time(9, 0), tzinfo=TZ))
    result = build_plan(
        TODAY, [yesterday, this_morning], ongoing(), events_by_day(), {}, PRESET_STUDENT_VN
    )
    assert result.overdue_task_ids == ("y",)
    assert "m" in result.infeasible_task_ids
    # Truyền now đúng giờ thì "m" mới được nhận là overdue
    now = datetime.combine(TODAY, time(12, 23), tzinfo=TZ)
    exact = build_plan(
        TODAY, [yesterday, this_morning], ongoing(), events_by_day(), {}, PRESET_STUDENT_VN, now
    )
    assert exact.overdue_task_ids == ("m", "y")
    assert exact.infeasible_task_ids == ()
