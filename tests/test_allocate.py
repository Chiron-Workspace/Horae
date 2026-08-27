"""Test cho scheduler_core.allocate. Dựng DayCapacity trực tiếp để kiểm soát capacity."""

import copy
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from scheduler_core.allocate import allocate_assignments, allocate_ongoing
from scheduler_core.config import DEFAULT_CONFIG, PRESET_STUDENT_VN, SchedulerConfig
from scheduler_core.models import Assignment, DayCapacity, Interval, OngoingTask

MON = date(2026, 1, 5)
TUE = date(2026, 1, 6)
WED = date(2026, 1, 7)
THU = date(2026, 1, 8)
FRI = date(2026, 1, 9)
SAT = date(2026, 1, 10)
SUN = date(2026, 1, 11)


def _cap(day, capacity_minutes, *, free_start=7, free_end=22, tz="UTC"):
    """DayCapacity đơn giản: free_intervals là một khối [free_start, free_end], ceiling=capacity."""
    z = ZoneInfo(tz)
    free = (Interval(datetime.combine(day, time(free_start), tzinfo=z),
                     datetime.combine(day, time(free_end), tzinfo=z)),)
    return DayCapacity(
        date=day,
        free_intervals=free,
        busy_minutes=0,
        ceiling_minutes=capacity_minutes,
        capacity_minutes=capacity_minutes,
        dropped_fragments=(),
        leisure_interval=None,
    )


def _caps(days, capacity, **kw):
    return {d: _cap(d, capacity, **kw) for d in days}


def _assignment(tid, deadline_date, estimate, *, done=0, title=None, tz="UTC"):
    z = ZoneInfo(tz)
    return Assignment(
        task_id=tid,
        title=title or tid,
        estimate_minutes=estimate,
        deadline=datetime.combine(deadline_date, time(21, 0), tzinfo=z),
        done_minutes=done,
    )


def _sum_task(alloc, tid):
    return sum(day_alloc.get(tid, 0) for day_alloc in alloc.values())


def _sum_day(alloc, day):
    return sum(alloc.get(day, {}).values())


def _task_days(alloc, tid):
    return sorted(d for d, da in alloc.items() if da.get(tid, 0) > 0)


# ---------------------------------------------------------------- nhánh A


def test_1_branch_a_sum_is_remaining_not_240():
    days = [MON, TUE, WED, THU, FRI]
    caps = _caps(days, 240)
    task = _assignment("t1", FRI, 180)
    alloc = allocate_assignments([task], caps, DEFAULT_CONFIG)
    assert _sum_task(alloc, "t1") == 180
    assert _sum_task(alloc, "t1") != 240


def test_2_branch_a_higher_capacity_gets_more():
    caps = {
        MON: _cap(MON, 240),
        TUE: _cap(TUE, 360),
        WED: _cap(WED, 360),
        THU: _cap(THU, 240),
        FRI: _cap(FRI, 240),
    }
    task = _assignment("t1", FRI, 180)
    alloc = allocate_assignments([task], caps, DEFAULT_CONFIG)
    # ngày capacity cao (TUE/WED) nhận nhiều hơn ngày capacity thấp (MON)
    assert alloc[TUE]["t1"] > alloc[MON]["t1"]
    assert alloc[WED]["t1"] > alloc[THU]["t1"]
    assert _sum_task(alloc, "t1") == 180


def test_3_all_nonzero_allocs_at_least_min():
    days = [MON, TUE, WED, THU, FRI]
    caps = _caps(days, 240)
    task = _assignment("t1", FRI, 180)
    alloc = allocate_assignments([task], caps, DEFAULT_CONFIG)
    for day_alloc in alloc.values():
        for m in day_alloc.values():
            assert m >= DEFAULT_CONFIG.blocks.min_minutes
            assert m not in (15, 20)


def test_4_branch_a_far_deadline_not_all_on_first_day():
    days = [MON + timedelta(days=i) for i in range(22)]
    caps = _caps(days, 240)
    task = _assignment("t1", days[-1], 60)
    alloc = allocate_assignments([task], caps, DEFAULT_CONFIG)
    assert _sum_task(alloc, "t1") == 60
    task_days = _task_days(alloc, "t1")
    assert len(task_days) > 1  # không dồn hết vào ngày đầu


def test_5_non_divisible_remaining_still_sums_to_remaining():
    days = [MON, TUE, WED, THU, FRI]
    caps = _caps(days, 240)
    # remaining 100 không chia hết cho allocation_unit 15
    task = _assignment("t1", FRI, 100)
    alloc = allocate_assignments([task], caps, DEFAULT_CONFIG)
    assert _sum_task(alloc, "t1") == 100


# ---------------------------------------------------------------- nhánh B


def test_6_branch_b_first_day_with_enough_room():
    days = [MON, TUE, WED]
    caps = _caps(days, 240)
    # 60 <= threshold 90, days_until 3 <= 7 → nhánh B
    task = _assignment("t1", WED, 60)
    alloc = allocate_assignments([task], caps, DEFAULT_CONFIG)
    assert alloc[MON]["t1"] == 60  # ngày đầu tiên có đủ chỗ
    assert _sum_task(alloc, "t1") == 60


def test_7_branch_b_second_day_when_first_insufficient():
    caps = {
        MON: _cap(MON, 30),   # không đủ cho 60
        TUE: _cap(TUE, 240),  # đủ
        WED: _cap(WED, 240),
    }
    task = _assignment("t1", WED, 60)
    alloc = allocate_assignments([task], caps, DEFAULT_CONFIG)
    assert alloc.get(MON, {}).get("t1", 0) == 0
    assert alloc[TUE]["t1"] == 60


def test_8_branch_b_no_day_fully_fits_split_into_min_parts():
    caps = {
        MON: _cap(MON, 30),
        TUE: _cap(TUE, 30),
        WED: _cap(WED, 30),
    }
    # remaining 90, không ngày nào >= 90 → chia theo thứ tự thời gian
    task = _assignment("t1", WED, 90)
    alloc = allocate_assignments([task], caps, DEFAULT_CONFIG)
    assert alloc[MON]["t1"] == 30
    assert alloc[TUE]["t1"] == 30
    assert alloc[WED]["t1"] == 30
    assert _sum_task(alloc, "t1") == 90


# ---------------------------------------------------------------- ưu tiên


def test_9_urgent_task_served_first():
    days = [MON, TUE, WED]
    caps = _caps(days, 120)  # mỗi ngày 120
    urgent = _assignment("A", TUE, 180)    # hạn gần hơn → phục vụ trước
    far = _assignment("B", WED, 60)        # hạn xa hơn
    alloc = allocate_assignments([urgent, far], caps, DEFAULT_CONFIG)
    # A phục vụ trước, lấy đủ 180 (nhánh A); B không chen lên lấy chỗ của A
    assert _sum_task(alloc, "A") == 180
    # B chỉ nhận phần còn dư; tổng A+B không vượt capacity tổng
    total = _sum_task(alloc, "A") + _sum_task(alloc, "B")
    assert total <= 3 * 120


def test_10_small_far_task_not_compressed_into_two_first_days():
    days = [MON + timedelta(days=i) for i in range(10)]
    caps = _caps(days, 240)
    big_urgent = _assignment("BIG", TUE, 210)        # hạn gần, phục vụ trước (nhánh A)
    small_far = _assignment("SML", days[-1], 90)     # hạn xa, raw 9'/ngày → nhánh A
    alloc = allocate_assignments([big_urgent, small_far], caps, DEFAULT_CONFIG)
    # small không bị nén vào 2 ngày đầu
    assert len(_task_days(alloc, "SML")) > 2
    # big vẫn nhận đủ (không bị small cướp chỗ)
    assert _sum_task(alloc, "BIG") == 210


def test_11_capacity_just_enough_assignment_gets_full():
    days = [MON, TUE, WED]
    caps = _caps(days, 60)  # tổng 180
    task = _assignment("t1", WED, 180)
    alloc = allocate_assignments([task], caps, DEFAULT_CONFIG)
    assert _sum_task(alloc, "t1") == 180  # vừa đủ → nhận đủ, không bị cắt


# ---------------------------------------------------------------- ongoing


def test_12_allocate_ongoing_does_not_mutate_assignment_alloc():
    days = [MON, TUE]
    caps = _caps(days, 240)
    task = _assignment("t1", TUE, 90)
    a_alloc = allocate_assignments([task], caps, DEFAULT_CONFIG)
    snapshot = copy.deepcopy(a_alloc)
    ongoing = [OngoingTask("o1", "daily", 60)]
    allocate_ongoing(ongoing, caps, a_alloc, {}, DEFAULT_CONFIG)
    assert a_alloc == snapshot      # không bị mutate


def test_12b_allocate_assignments_is_deterministic():
    days = [MON, TUE, WED]
    caps = _caps(days, 240)
    task = _assignment("t1", WED, 90)
    alloc1 = allocate_assignments([task], caps, DEFAULT_CONFIG)
    alloc2 = allocate_assignments([task], caps, DEFAULT_CONFIG)
    assert alloc1 == alloc2


def test_13_ongoing_gets_leftover_after_assignment():
    days = [MON, TUE]
    caps = _caps(days, 240)
    task = _assignment("t1", TUE, 90)  # nhận 90 vào ngày nào đó
    a_alloc = allocate_assignments([task], caps, DEFAULT_CONFIG)
    ongoing = [OngoingTask("o1", "daily", 60)]
    o_alloc = allocate_ongoing(ongoing, caps, a_alloc, {}, DEFAULT_CONFIG)
    assign_used_mon = _sum_day(a_alloc, MON)
    assign_used_tue = _sum_day(a_alloc, TUE)
    for day in days:
        assert _sum_day(o_alloc, day) <= caps[day].capacity_minutes - _sum_day(a_alloc, day)
    # ongoing nhận phần dư thật (không âm)
    given = [m for da in o_alloc.values() for m in da.values()]
    assert len(given) > 0                    # ongoing thực sự nhận được gì đó
    assert all(m >= DEFAULT_CONFIG.blocks.min_minutes for m in given)


def test_14_ongoing_zero_when_leftover_below_min():
    day = MON
    cap = _cap(day, 40)  # capacity 40, min 30 → leftover 40 đủ, nhưng nếu assignment lấy hết thì < min
    caps = {day: cap}
    task = _assignment("t1", day, 40)  # chiếm hết capacity → leftover 0
    a_alloc = allocate_assignments([task], caps, DEFAULT_CONFIG)
    ongoing = [OngoingTask("o1", "daily", 60)]
    o_alloc = allocate_ongoing(ongoing, caps, a_alloc, {}, DEFAULT_CONFIG)
    assert o_alloc == {} or _sum_day(o_alloc, day) == 0


def test_15_two_ongoing_room_for_one_alphabetical_first():
    day = MON
    caps = {day: _cap(day, 120)}  # đủ cho 1 ongoing 60 + existing/chừa
    # assignment không chiếm gì → leftover 120, đủ cho hai 60... đặt target lớn để chỉ đủ 1
    a_alloc = {}
    ongoing = [
        OngoingTask("zeta", "Zeta", 120),
        OngoingTask("alpha", "Alpha", 120),
    ]
    o_alloc = allocate_ongoing(ongoing, caps, a_alloc, {}, DEFAULT_CONFIG)
    # alpha (đầu bảng chữ cái) nhận đủ 120, zeta nhận 0
    assert o_alloc[day].get("alpha", 0) == 120
    assert o_alloc[day].get("zeta", 0) == 0


def test_16_existing_blocks_reduce_leftover():
    day = MON
    caps = {day: _cap(day, 240)}
    a_alloc = {}
    existing = {day: 60}
    ongoing = [OngoingTask("o1", "daily", 120)]
    o_alloc = allocate_ongoing(ongoing, caps, a_alloc, existing, DEFAULT_CONFIG)
    # leftover = 240 - 0 - 60 = 180 → o1 nhận min(120,180)=120
    assert o_alloc[day]["o1"] == 120


def test_17_assignment_plus_ongoing_within_ceiling():
    day = MON
    caps = {day: _cap(day, 240)}
    task = _assignment("t1", day, 120)
    a_alloc = allocate_assignments([task], caps, DEFAULT_CONFIG)
    ongoing = [OngoingTask("o1", "daily", 120)]
    o_alloc = allocate_ongoing(ongoing, caps, a_alloc, {}, DEFAULT_CONFIG)
    total = _sum_day(a_alloc, day) + _sum_day(o_alloc, day)
    assert total <= caps[day].ceiling_minutes


# ---------------------------------------------------------------- không hardcode


def test_18_allocation_unit_5_finer_still_correct():
    days = [MON, TUE, WED, THU, FRI]
    caps = _caps(days, 240)
    cfg = replace(DEFAULT_CONFIG, blocks=replace(DEFAULT_CONFIG.blocks, allocation_unit=5))
    task = _assignment("t1", FRI, 180)
    alloc = allocate_assignments([task], caps, cfg)
    assert _sum_task(alloc, "t1") == 180


def test_19_lower_threshold_moves_task_to_branch_a():
    days = [MON, TUE, WED]
    caps = _caps(days, 240)
    task = _assignment("t1", WED, 60)

    # threshold mặc định 90 → 60 <= 90 → nhánh B → dồn một khối vào ngày đầu
    alloc_b = allocate_assignments([task], caps, DEFAULT_CONFIG)
    assert len(_task_days(alloc_b, "t1")) == 1
    assert alloc_b[MON]["t1"] == 60

    # threshold 30 → 60 > 30 → nhánh A → chia trên nhiều ngày (30+30)
    cfg = replace(DEFAULT_CONFIG, small_task_threshold=30)
    alloc_a = allocate_assignments([task], caps, cfg)
    assert _sum_task(alloc_a, "t1") == 60
    assert len(_task_days(alloc_a, "t1")) > 1
    assert alloc_a != alloc_b


def test_20_higher_deadline_days_moves_task_to_branch_b():
    days = [MON + timedelta(days=i) for i in range(22)]
    caps = _caps(days, 240)
    cfg = replace(DEFAULT_CONFIG, small_task_deadline_days=30)
    # 60 <= 90, days_until 22 <= 30 (mới) → nhánh B → hết vào ngày đầu đủ chỗ
    task = _assignment("t1", days[-1], 60)
    alloc = allocate_assignments([task], caps, cfg)
    assert _sum_task(alloc, "t1") == 60
    assert len(_task_days(alloc, "t1")) == 1  # nhánh B: một khối trọn
    assert alloc[days[0]]["t1"] == 60


# ---------------------------------------------------------------- shortfall


def test_21_no_shortfall_when_enough_capacity():
    days = [MON, TUE, WED, THU, FRI]
    caps = _caps(days, 240)
    task = _assignment("t1", FRI, 100)
    result = allocate_assignments([task], caps, DEFAULT_CONFIG)
    assert _sum_task(result, "t1") == 100
    assert result.shortfall == {}


def test_22_shortfill_when_capacity_too_tight():
    # 5 ngày × fc=20' mỗi ngày → tất cả < min=30 → không xếp được gì
    days = [MON + timedelta(days=i) for i in range(5)]
    caps = _caps(days, 20)
    task = _assignment("t1", days[-1], 100)
    result = allocate_assignments([task], caps, DEFAULT_CONFIG)
    assert _sum_task(result, "t1") == 0  # không ngày nào chứa nổi
    assert result.shortfall.get("t1", 0) == 100
    assert result.shortfall != {}


def test_23_residual_not_clipped_goes_to_other_day():
    # Xây dựng ca mà phiên bản cũ clip residual:
    # 2 ngày, fc=[32, 50], total=65, unit=15 → day0 nhận 30+5=35 > fc=32 → clip mất 3'
    # Phiên bản mới: residual 5' đi sang day1 → Σ == 65, shortfall rỗng
    caps = {MON: _cap(MON, 32), TUE: _cap(TUE, 50)}
    task = _assignment("t1", TUE, 65)
    result = allocate_assignments([task], caps, DEFAULT_CONFIG)
    assert _sum_task(result, "t1") == 65
    assert result.shortfall == {}


def test_24_overload_shortfall_equals_remaining_minus_available():
    # 3 ngày, fc=[40, 40, 20], remaining=200 → quá tải
    # Σ = 40+40 = 80 (day2 có fc=20 < min=30 → 0), shortfall = 200-80 = 120
    caps = {MON: _cap(MON, 40), TUE: _cap(TUE, 40), WED: _cap(WED, 20)}
    task = _assignment("t1", WED, 200)
    result = allocate_assignments([task], caps, DEFAULT_CONFIG)
    assert _sum_task(result, "t1") == 80
    assert result.shortfall.get("t1", 0) == 120


def test_25_invariant_sum_plus_shortfall_equals_remaining():
    """Σ phân_bổ + shortfall == remaining cho mọi bài test."""
    scenarios = [
        ([MON, TUE, WED, THU, FRI], {d: 240 for d in [MON, TUE, WED, THU, FRI]}, "t1", FRI, 100),
        ([MON, TUE, WED], {MON: 240, TUE: 360, WED: 360}, "t1", WED, 180),
        ([MON, TUE, WED], {MON: 30, TUE: 30, WED: 30}, "t1", WED, 90),
        ([MON, TUE], {MON: 32, TUE: 50}, "t1", TUE, 65),
        ([MON, TUE, WED], {MON: 40, TUE: 40, WED: 20}, "t1", WED, 200),
        ([MON + timedelta(days=i) for i in range(22)], {d: 240 for d in [MON + timedelta(days=i) for i in range(22)]}, "t1", MON + timedelta(days=21), 60),
    ]
    for days, fc_map, tid, deadline, estimate in scenarios:
        caps = {d: _cap(d, fc_map[d]) for d in days}
        task = _assignment(tid, deadline, estimate)
        result = allocate_assignments([task], caps, DEFAULT_CONFIG)
        allocated = _sum_task(result, tid)
        shortfall = result.shortfall.get(tid, 0)
        assert allocated + shortfall == estimate, (
            f"Scenario tid={tid} est={estimate}: allocated={allocated} + shortfall={shortfall} != {estimate}"
        )


# ---------------------------------------------------------------- min blocks chronological


def test_30_min_blocks_prefer_earliest_days():
    # 60' rải trên 10 ngày, fc: ngày 0–2 = 60', ngày 3–9 = 240'
    # → hai khối 30' rơi vào ngày 0 và 1, KHÔNG phải ngày 8 và 9
    days = [MON + timedelta(days=i) for i in range(10)]
    caps = {}
    for i, d in enumerate(days):
        caps[d] = _cap(d, 60 if i <= 2 else 240)
    task = _assignment("t1", days[-1], 60)
    result = allocate_assignments([task], caps, DEFAULT_CONFIG)
    task_days = _task_days(result, "t1")
    assert task_days == [days[0], days[1]]  # ngày sớm nhất, không phải ngày fc cao
