"""Test cho scheduler_core.capacity (PRESET_STUDENT_VN trừ khi test nói khác)."""

from dataclasses import replace
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from scheduler_core.capacity import compute_day_capacity
from scheduler_core.config import (
    DEFAULT_CONFIG,
    PRESET_STUDENT_VN,
    BreakWindow,
    CeilingRule,
    DayWindow,
    LeisureRule,
    TravelRule,
)
from scheduler_core.intervals import subtract, total_minutes
from scheduler_core.models import FixedEvent, Interval

MON = date(2026, 1, 5)    # Monday    (weekday 0)
WED = date(2026, 1, 7)    # Wednesday (weekday 2)
SAT = date(2026, 1, 10)   # Saturday  (weekday 5)
SUN = date(2026, 1, 11)   # Sunday    (weekday 6)


# ------------------------------------------------------------------ helpers


def _tz(cfg):
    return ZoneInfo(cfg.timezone)


def mk_interval(cfg, day, sh, sm, eh, em):
    return Interval(
        datetime.combine(day, time(sh, sm), tzinfo=_tz(cfg)),
        datetime.combine(day, time(eh, em), tzinfo=_tz(cfg)),
    )


def mk_event(cfg, day, sh, sm, eh, em, *, travel=False, opaque=True, all_day=False, title="e"):
    return FixedEvent(
        title=title,
        start=datetime.combine(day, time(sh, sm), tzinfo=_tz(cfg)),
        end=datetime.combine(day, time(eh, em), tzinfo=_tz(cfg)),
        requires_travel=travel,
        is_opaque=opaque,
        is_all_day=all_day,
    )


def is_blocked(cfg, cap, sh, sm, eh, em):
    """Region [sh:sm, eh:em] hoàn toàn không giao free → bị chặn."""
    region = mk_interval(cfg, cap.date, sh, sm, eh, em)
    return subtract(cap.free_intervals, (region,)) == cap.free_intervals


def free_total(cap):
    return total_minutes(cap.free_intervals)


# ------------------------------------------------------------------ travel


def test_1_single_travel_padding_and_recovery():
    cfg = PRESET_STUDENT_VN
    cap = compute_day_capacity(MON, [mk_event(cfg, MON, 14, 0, 17, 0, travel=True)], cfg)
    assert cap.free_intervals == (mk_interval(cfg, MON, 7, 0, 12, 0), mk_interval(cfg, MON, 20, 0, 22, 0))
    assert cap.dropped_fragments == (mk_interval(cfg, MON, 18, 45, 19, 0),)
    assert is_blocked(cfg, cap, 12, 0, 14, 0)      # pre (trùng lunch)
    assert is_blocked(cfg, cap, 17, 0, 18, 15)     # post
    assert is_blocked(cfg, cap, 18, 15, 18, 45)    # recovery


def test_2_chain_subtracts_full_gap():
    cfg = PRESET_STUDENT_VN
    events = [
        mk_event(cfg, MON, 14, 0, 17, 0, travel=True),
        mk_event(cfg, MON, 18, 0, 20, 0, travel=True),
    ]
    cap = compute_day_capacity(MON, events, cfg)
    # gap 17:00–18:00 bị trừ trọn; pre chỉ trước 14:00; post chỉ sau 20:00; recovery sau post
    assert is_blocked(cfg, cap, 17, 0, 18, 0)       # khoảng giữa chuỗi
    assert is_blocked(cfg, cap, 12, 0, 14, 0)       # pre (trùng lunch)
    assert is_blocked(cfg, cap, 20, 0, 21, 15)      # post
    assert is_blocked(cfg, cap, 21, 15, 21, 45)     # recovery
    # recovery kết thúc 21:45 → mảnh 21:45–22:00 chỉ 15' bị drop
    assert cap.free_intervals == (mk_interval(cfg, MON, 7, 0, 12, 0),)
    assert cap.dropped_fragments == (mk_interval(cfg, MON, 21, 45, 22, 0),)


def test_3_gap_equal_threshold_is_not_a_chain():
    # Config travel riêng (test nói khác): pre/post nhỏ để thấy gap KHÔNG bị trừ trọn
    cfg = replace(PRESET_STUDENT_VN, travel=TravelRule(15, 15, 0, 60))
    events = [
        mk_event(cfg, MON, 14, 0, 15, 0, travel=True),
        mk_event(cfg, MON, 16, 0, 17, 0, travel=True),  # gap 60' == threshold → không thành chuỗi
    ]
    cap = compute_day_capacity(MON, events, cfg)
    # gap 15:00–16:00 KHÔNG bị trừ trọn: còn mảnh 15:15–15:45
    assert mk_interval(cfg, MON, 15, 15, 15, 45) in cap.free_intervals
    assert is_blocked(cfg, cap, 15, 0, 15, 15)      # post của event 1
    assert is_blocked(cfg, cap, 15, 45, 16, 0)      # pre của event 2 (riêng)

    # Tương phản: gap 59' (< threshold) → thành chuỗi → gap bị trừ trọn
    cfg_chain = replace(cfg, travel=TravelRule(15, 15, 0, 61))
    cap_chain = compute_day_capacity(MON, events, cfg_chain)
    assert is_blocked(cfg_chain, cap_chain, 15, 0, 16, 0)
    assert mk_interval(cfg_chain, MON, 15, 15, 15, 45) not in cap_chain.free_intervals


def test_4_transitive_chain_three_events():
    cfg = replace(PRESET_STUDENT_VN, travel=TravelRule(15, 15, 0, 60))
    events = [
        mk_event(cfg, MON, 14, 0, 15, 0, travel=True),       # A
        mk_event(cfg, MON, 15, 45, 16, 45, travel=True),     # B (gap 45 < 60)
        mk_event(cfg, MON, 17, 30, 18, 30, travel=True),     # C (gap 45 < 60)
    ]
    cap = compute_day_capacity(MON, events, cfg)
    # Một chuỗi duy nhất: cả hai gap bị trừ trọn, không có pre riêng cho B hay C
    assert is_blocked(cfg, cap, 15, 0, 15, 45)      # gap A–B
    assert is_blocked(cfg, cap, 16, 45, 17, 30)     # gap B–C
    assert cap.free_intervals == (mk_interval(cfg, MON, 7, 0, 12, 0), mk_interval(cfg, MON, 20, 0, 22, 0))
    assert cap.dropped_fragments == (mk_interval(cfg, MON, 18, 45, 19, 0),)


def test_5_non_travel_event_has_no_padding():
    cfg = PRESET_STUDENT_VN
    cap = compute_day_capacity(MON, [mk_event(cfg, MON, 14, 0, 17, 0, travel=False)], cfg)
    assert cap.free_intervals == (
        mk_interval(cfg, MON, 7, 0, 12, 0),
        mk_interval(cfg, MON, 17, 0, 19, 0),
        mk_interval(cfg, MON, 20, 0, 22, 0),
    )
    # 17:00–18:15 vẫn FREE (không có post)
    assert not is_blocked(cfg, cap, 17, 0, 18, 15)


def test_6_recovery_outside_day_window_is_silent():
    cfg = PRESET_STUDENT_VN
    cap = compute_day_capacity(MON, [mk_event(cfg, MON, 21, 0, 21, 30, travel=True)], cfg)
    # recovery 22:45–23:15 rơi ngoài khung 22:00 → không cắt gì, không raise
    assert cap.free_intervals == (mk_interval(cfg, MON, 7, 0, 12, 0), mk_interval(cfg, MON, 14, 0, 19, 0))


# ------------------------------------------------------------------ busy


def test_7_busy_clipped_to_day_end():
    cfg = PRESET_STUDENT_VN
    cap = compute_day_capacity(MON, [mk_event(cfg, MON, 21, 30, 22, 45, travel=False)], cfg)
    assert cap.busy_minutes == 30


def test_8_busy_merges_overlapping_events():
    cfg = PRESET_STUDENT_VN
    events = [
        mk_event(cfg, MON, 20, 0, 21, 0, travel=False),
        mk_event(cfg, MON, 20, 0, 21, 30, travel=False),
    ]
    cap = compute_day_capacity(MON, events, cfg)
    assert cap.busy_minutes == 90


def test_9_all_day_event_not_subtracted_not_busy():
    cfg = PRESET_STUDENT_VN
    cap = compute_day_capacity(MON, [mk_event(cfg, MON, 10, 0, 12, 0, all_day=True)], cfg)
    assert cap.busy_minutes == 0
    # 10:00–12:00 vẫn FREE (chỉ trừ break lunch 12:00–14:00)
    assert cap.free_intervals == (
        mk_interval(cfg, MON, 7, 0, 12, 0),
        mk_interval(cfg, MON, 14, 0, 19, 0),
        mk_interval(cfg, MON, 20, 0, 22, 0),
    )


def test_10_non_opaque_event_not_subtracted_not_busy():
    cfg = PRESET_STUDENT_VN
    cap = compute_day_capacity(MON, [mk_event(cfg, MON, 10, 0, 12, 0, opaque=False)], cfg)
    assert cap.busy_minutes == 0
    assert cap.free_intervals == (
        mk_interval(cfg, MON, 7, 0, 12, 0),
        mk_interval(cfg, MON, 14, 0, 19, 0),
        mk_interval(cfg, MON, 20, 0, 22, 0),
    )


# ------------------------------------------------------------------ fragments


def test_11_fragment_below_min_is_dropped():
    cfg = PRESET_STUDENT_VN
    cap = compute_day_capacity(MON, [mk_event(cfg, MON, 14, 0, 17, 0, travel=True)], cfg)
    assert mk_interval(cfg, MON, 18, 45, 19, 0) in cap.dropped_fragments
    assert cap.dropped_fragments[0].duration_minutes == 15
    assert all(iv.duration_minutes >= 30 for iv in cap.free_intervals)


def test_12_fragment_exactly_min_is_kept():
    cfg = PRESET_STUDENT_VN
    cap = compute_day_capacity(MON, [mk_event(cfg, MON, 14, 45, 16, 45, travel=True)], cfg)
    assert mk_interval(cfg, MON, 18, 30, 19, 0) in cap.free_intervals
    assert mk_interval(cfg, MON, 18, 30, 19, 0).duration_minutes == 30


# ------------------------------------------------------------------ leisure


def test_13_leisure_placement_latest_vs_earliest():
    cfg_latest = PRESET_STUDENT_VN
    cfg_earliest = replace(PRESET_STUDENT_VN, leisure=replace(PRESET_STUDENT_VN.leisure, placement="earliest"))
    cap_latest = compute_day_capacity(SAT, [], cfg_latest)
    cap_earliest = compute_day_capacity(SAT, [], cfg_earliest)
    assert cap_latest.leisure_interval == mk_interval(cfg_latest, SAT, 16, 0, 19, 0)
    assert cap_earliest.leisure_interval == mk_interval(cfg_earliest, SAT, 7, 0, 10, 0)


def test_14_leisure_no_block_long_enough_takes_longest():
    cfg = replace(PRESET_STUDENT_VN, leisure=LeisureRule(400, 0, frozenset({5, 6}), "latest"))
    cap = compute_day_capacity(SAT, [], cfg)
    # không có interval nào >= 400 → lấy longest (07:00–12:00, 300')
    assert cap.leisure_interval == mk_interval(cfg, SAT, 7, 0, 12, 0)


def test_15_weekday_has_no_leisure():
    cfg = PRESET_STUDENT_VN
    cap = compute_day_capacity(MON, [], cfg)
    assert cap.leisure_interval is None


# ------------------------------------------------------------------ ceiling


def test_16_busy_at_threshold_uses_ceiling_busy():
    cfg = PRESET_STUDENT_VN
    cap_at = compute_day_capacity(MON, [mk_event(cfg, MON, 10, 0, 14, 0, travel=False)], cfg)
    assert cap_at.busy_minutes == 240
    assert cap_at.ceiling_minutes == cfg.ceiling.ceiling_busy  # >= threshold
    cap_below = compute_day_capacity(MON, [mk_event(cfg, MON, 10, 0, 13, 59, travel=False)], cfg)
    assert cap_below.busy_minutes == 239
    assert cap_below.ceiling_minutes == cfg.ceiling.ceiling_free


# ------------------------------------------------------------------ deadline


def test_17_deadline_cutoff_clips_free():
    cfg = PRESET_STUDENT_VN
    cutoff = datetime.combine(MON, time(21, 0), tzinfo=_tz(cfg))
    cap = compute_day_capacity(MON, [mk_event(cfg, MON, 21, 30, 22, 0, travel=False)], cfg, deadline_cutoff=cutoff)
    assert cap.free_intervals == (
        mk_interval(cfg, MON, 7, 0, 12, 0),
        mk_interval(cfg, MON, 14, 0, 19, 0),
        mk_interval(cfg, MON, 20, 0, 21, 0),  # 20:00–21:30 cắt còn 20:00–21:00
    )
    assert mk_interval(cfg, MON, 21, 0, 21, 30) not in cap.free_intervals


# ------------------------------------------------------------------ missing window


def test_18_missing_day_window_returns_empty():
    cfg = replace(PRESET_STUDENT_VN, day_windows={d: DayWindow(time(7, 0), time(22, 0)) for d in range(6)})
    cap = compute_day_capacity(SUN, [], cfg)
    assert cap.free_intervals == ()
    assert cap.capacity_minutes == 0
    assert cap.busy_minutes == 0
    assert cap.leisure_interval is None


# ------------------------------------------------------------------ no hardcode


def test_19_day_window_shift_moves_free():
    cfg = replace(
        PRESET_STUDENT_VN, day_windows={d: DayWindow(time(9, 0), time(23, 0)) for d in range(7)}
    )
    cap = compute_day_capacity(MON, [], cfg)
    assert cap.free_intervals == (
        mk_interval(cfg, MON, 9, 0, 12, 0),
        mk_interval(cfg, MON, 14, 0, 19, 0),
        mk_interval(cfg, MON, 20, 0, 23, 0),
    )


def test_20_no_leisure_means_none_even_weekend_and_higher_capacity():
    # ceiling cao (test nói khác) để capacity = free, thấy rõ leisure giảm capacity
    high_ceiling = CeilingRule(240, 240, 720)
    cfg_none = replace(PRESET_STUDENT_VN, leisure=None, ceiling=high_ceiling)
    cfg_with = replace(PRESET_STUDENT_VN, ceiling=high_ceiling)
    event = mk_event(cfg_none, SAT, 14, 0, 17, 20, travel=False)  # 200', busy < threshold
    cap_none = compute_day_capacity(SAT, [event], cfg_none)
    cap_with = compute_day_capacity(SAT, [mk_event(cfg_with, SAT, 14, 0, 17, 20, travel=False)], cfg_with)
    assert cap_none.leisure_interval is None
    assert cap_none.capacity_minutes == 520
    assert cap_with.capacity_minutes == 340
    assert cap_none.capacity_minutes > cap_with.capacity_minutes


def test_21_smaller_pre_shortens_block_by_exactly_90():
    cfg_120 = PRESET_STUDENT_VN
    cfg_30 = replace(PRESET_STUDENT_VN, travel=replace(PRESET_STUDENT_VN.travel, pre_minutes=30))
    event_120 = mk_event(cfg_120, MON, 9, 0, 10, 0, travel=True)
    event_30 = mk_event(cfg_30, MON, 9, 0, 10, 0, travel=True)
    cap_120 = compute_day_capacity(MON, [event_120], cfg_120)
    cap_30 = compute_day_capacity(MON, [event_30], cfg_30)
    assert free_total(cap_30) - free_total(cap_120) == 90
    # 07:00–08:30 tự do khi pre=30, nhưng bị chặn khi pre=120
    assert mk_interval(cfg_30, MON, 7, 0, 8, 30) in cap_30.free_intervals
    assert is_blocked(cfg_120, cap_120, 7, 0, 8, 30)


def test_22_extra_break_only_applies_on_its_day():
    cfg = replace(
        PRESET_STUDENT_VN,
        breaks=PRESET_STUDENT_VN.breaks + (BreakWindow("nap", time(15, 0), time(15, 30), frozenset({2})),),
    )
    cap_wed = compute_day_capacity(WED, [], cfg)
    cap_mon = compute_day_capacity(MON, [], cfg)
    assert cap_wed.free_intervals == (
        mk_interval(cfg, WED, 7, 0, 12, 0),
        mk_interval(cfg, WED, 14, 0, 15, 0),
        mk_interval(cfg, WED, 15, 30, 19, 0),
        mk_interval(cfg, WED, 20, 0, 22, 0),
    )
    assert cap_mon.free_intervals == (
        mk_interval(cfg, MON, 7, 0, 12, 0),
        mk_interval(cfg, MON, 14, 0, 19, 0),
        mk_interval(cfg, MON, 20, 0, 22, 0),
    )


def test_23_default_and_preset_differ_in_capacity():
    event_def = mk_event(DEFAULT_CONFIG, MON, 10, 0, 17, 0, travel=True)
    event_pre = mk_event(PRESET_STUDENT_VN, MON, 10, 0, 17, 0, travel=True)
    cap_def = compute_day_capacity(MON, [event_def], DEFAULT_CONFIG)
    cap_pre = compute_day_capacity(MON, [event_pre], PRESET_STUDENT_VN)
    assert cap_def.capacity_minutes == 240
    assert cap_pre.capacity_minutes == 180
    assert cap_def.capacity_minutes != cap_pre.capacity_minutes
