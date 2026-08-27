"""Test cho scheduler_core.blocks (cut_blocks)."""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from scheduler_core.blocks import cut_blocks, _split_part
from scheduler_core.config import DEFAULT_CONFIG
from scheduler_core.models import Assignment, DayCapacity, Interval, OngoingTask

MON = date(2026, 1, 5)
TZ = ZoneInfo("UTC")


def mk_interval(day, sh, sm, eh, em):
    return Interval(
        datetime.combine(day, time(sh, sm), tzinfo=TZ),
        datetime.combine(day, time(eh, em), tzinfo=TZ),
    )


def mk_capacity(day, intervals, *, ceiling=360):
    free = tuple(intervals)
    return DayCapacity(
        date=day,
        free_intervals=free,
        busy_minutes=0,
        ceiling_minutes=ceiling,
        capacity_minutes=sum(iv.duration_minutes for iv in free),
        dropped_fragments=(),
        leisure_interval=None,
    )


def mk_assignment(tid, deadline_dt, estimate, *, title=None):
    return Assignment(task_id=tid, title=title or tid, estimate_minutes=estimate, deadline=deadline_dt)


def mk_ongoing(tid, target, *, title=None):
    return OngoingTask(task_id=tid, title=title or tid, daily_target_minutes=target)


# ---------------------------------------------------------------- split


def test_1_single_block_when_part_within_max():
    assert _split_part(60, 30, 90) == [60]


def test_2_split_smallest_count_evenly():
    sizes = _split_part(135, 30, 90)
    assert sum(sizes) == 135
    assert len(sizes) == 2  # ceil(135/90) = 2, smallest valid count
    assert all(30 <= s <= 90 for s in sizes)
    assert max(sizes) - min(sizes) <= 1  # chênh nhau tối đa 1 phút


# ---------------------------------------------------------------- placement


def test_3_same_task_gap():
    from dataclasses import replace
    cfg = replace(DEFAULT_CONFIG, blocks=replace(DEFAULT_CONFIG.blocks, round_start_to=None))
    cap = mk_capacity(MON, [mk_interval(MON, 7, 0, 22, 0)])
    tasks = {"t1": mk_assignment("t1", datetime.combine(MON, time(21, 0), tzinfo=TZ), 120)}
    blocks = cut_blocks(cap, {"t1": 120}, tasks, cfg)
    assert len(blocks) == 2
    gap = (blocks[1].start - blocks[0].end).total_seconds() / 60
    assert gap == cfg.blocks.same_task_gap


def test_4_switch_task_gap():
    from dataclasses import replace
    cfg = replace(DEFAULT_CONFIG, blocks=replace(DEFAULT_CONFIG.blocks, round_start_to=None))
    cap = mk_capacity(MON, [mk_interval(MON, 7, 0, 22, 0)])
    deadline1 = datetime.combine(MON, time(21, 0), tzinfo=TZ)
    deadline2 = datetime.combine(MON, time(20, 0), tzinfo=TZ)  # sớm hơn → đặt trước
    tasks = {
        "t1": mk_assignment("t1", deadline1, 60),
        "t2": mk_assignment("t2", deadline2, 60),
    }
    blocks = cut_blocks(cap, {"t1": 60, "t2": 60}, tasks, cfg)
    assert len(blocks) == 2
    gap = (blocks[1].start - blocks[0].end).total_seconds() / 60
    assert gap == cfg.blocks.switch_task_gap


def test_5_assignment_before_ongoing():
    cap = mk_capacity(MON, [mk_interval(MON, 7, 0, 22, 0)])
    tasks = {
        "assign1": mk_assignment("assign1", datetime.combine(MON, time(21, 0), tzinfo=TZ), 60),
        "ongoing1": mk_ongoing("ongoing1", 60),
    }
    blocks = cut_blocks(cap, {"assign1": 60, "ongoing1": 60}, tasks, DEFAULT_CONFIG)
    assert blocks[0].kind == "assignment"
    assert blocks[1].kind == "ongoing"


def test_6_closer_deadline_first():
    cap = mk_capacity(MON, [mk_interval(MON, 7, 0, 22, 0)])
    near = datetime.combine(MON, time(18, 0), tzinfo=TZ)
    far = datetime.combine(MON, time(21, 0), tzinfo=TZ)
    tasks = {
        "far_t": mk_assignment("far_t", far, 60),
        "near_t": mk_assignment("near_t", near, 60),
    }
    blocks = cut_blocks(cap, {"far_t": 60, "near_t": 60}, tasks, DEFAULT_CONFIG)
    assert blocks[0].task_id == "near_t"
    assert blocks[1].task_id == "far_t"


def test_7_earliest_fit_at_fragment_start():
    cap = mk_capacity(MON, [mk_interval(MON, 9, 0, 12, 0)])
    tasks = {"t1": mk_assignment("t1", datetime.combine(MON, time(21, 0), tzinfo=TZ), 60)}
    blocks = cut_blocks(cap, {"t1": 60}, tasks, DEFAULT_CONFIG)
    assert blocks[0].start == datetime.combine(MON, time(9, 0), tzinfo=TZ)


def test_8_round_start_to_multiple_of_30():
    cap = mk_capacity(MON, [mk_interval(MON, 9, 15, 12, 0)])
    tasks = {"t1": mk_assignment("t1", datetime.combine(MON, time(21, 0), tzinfo=TZ), 60)}
    blocks = cut_blocks(cap, {"t1": 60}, tasks, DEFAULT_CONFIG)
    # 09:15 → round lên 09:30 (bội 30), còn dư chỗ
    assert blocks[0].start.minute % 30 == 0
    assert blocks[0].start == datetime.combine(MON, time(9, 30), tzinfo=TZ)


def test_9_round_when_fragment_just_fits_keep_odd():
    # mảnh 09:15–10:15 (60'), size 60 → round 09:30+60=10:30 > 10:15 → giữ 09:15
    cap = mk_capacity(MON, [mk_interval(MON, 9, 15, 10, 15)])
    tasks = {"t1": mk_assignment("t1", datetime.combine(MON, time(21, 0), tzinfo=TZ), 60)}
    blocks = cut_blocks(cap, {"t1": 60}, tasks, DEFAULT_CONFIG)
    assert blocks[0].start == datetime.combine(MON, time(9, 15), tzinfo=TZ)


def test_10_no_rounding_when_none():
    from dataclasses import replace
    cfg = replace(DEFAULT_CONFIG, blocks=replace(DEFAULT_CONFIG.blocks, round_start_to=None))
    cap = mk_capacity(MON, [mk_interval(MON, 9, 15, 11, 0)])
    tasks = {"t1": mk_assignment("t1", datetime.combine(MON, time(21, 0), tzinfo=TZ), 60)}
    blocks = cut_blocks(cap, {"t1": 60}, tasks, cfg)
    assert blocks[0].start == datetime.combine(MON, time(9, 15), tzinfo=TZ)


def test_11_assignment_block_before_deadline_same_day():
    deadline = datetime.combine(MON, time(21, 0), tzinfo=TZ)
    cap = mk_capacity(MON, [mk_interval(MON, 19, 0, 22, 0)])
    tasks = {"t1": mk_assignment("t1", deadline, 60)}
    blocks = cut_blocks(cap, {"t1": 60}, tasks, DEFAULT_CONFIG)
    assert len(blocks) == 1
    assert blocks[0].end < deadline


def test_12_all_blocks_within_free_intervals():
    free = [mk_interval(MON, 7, 0, 12, 0), mk_interval(MON, 14, 0, 22, 0)]
    cap = mk_capacity(MON, free)
    tasks = {
        "t1": mk_assignment("t1", datetime.combine(MON, time(21, 0), tzinfo=TZ), 120),
        "t2": mk_ongoing("t2", 60),
    }
    blocks = cut_blocks(cap, {"t1": 120, "t2": 60}, tasks, DEFAULT_CONFIG)
    for b in blocks:
        contained = any(iv.start <= b.start and b.end <= iv.end for iv in free)
        assert contained, f"block {b.title} {b.start:%H:%M}–{b.end:%H:%M} không nằm trong free"


# ---------------------------------------------------------------- quy tắc chia block mới


def test_35_split_135_unit_15():
    sizes = _split_part(135, 30, 90, 15)
    assert sizes == [75, 60]


def test_36_split_120_unit_15():
    sizes = _split_part(120, 30, 90, 15)
    assert sizes == [60, 60]


def test_37_split_100_unit_15_no_multiple_split():
    sizes = _split_part(100, 30, 90, 15)
    assert sizes == [50, 50]  # 100 không chia hết 15 → bỏ ràng buộc unit → đều nhau nhất


def test_38_split_60_single_block():
    sizes = _split_part(60, 30, 90, 15)
    assert sizes == [60]


def test_39_split_100_unit_5():
    sizes = _split_part(100, 30, 90, 5)
    assert sizes == [50, 50]  # 100 chia hết 5 → [50, 50] vẫn đúng
