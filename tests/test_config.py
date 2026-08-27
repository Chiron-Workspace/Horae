"""Test cho scheduler_core.config: round-trip, 11 luật validate, preset, ngày thiếu window."""

import json
from dataclasses import replace
from datetime import date, time
from zoneinfo import ZoneInfo

import pytest

from scheduler_core.config import (
    DEFAULT_CONFIG,
    PRESET_STUDENT_VN,
    BlockRule,
    BreakWindow,
    CeilingRule,
    ConfigError,
    DayWindow,
    LeisureRule,
    SchedulerConfig,
    TravelRule,
)


def full_week() -> dict[int, DayWindow]:
    return {day: DayWindow(time(7, 0), time(22, 0)) for day in range(7)}


# ---------------------------------------------------------------- round-trip


@pytest.mark.parametrize("config", [DEFAULT_CONFIG, PRESET_STUDENT_VN])
def test_to_dict_from_dict_round_trip(config):
    assert SchedulerConfig.from_dict(config.to_dict()) == config


@pytest.mark.parametrize("config", [DEFAULT_CONFIG, PRESET_STUDENT_VN])
def test_round_trip_through_json(config):
    # to_dict phải là JSON thuần: dumps rồi loads vẫn round-trip bằng chính nó
    as_json = json.loads(json.dumps(config.to_dict()))
    assert SchedulerConfig.from_dict(as_json) == config


def test_config_error_is_value_error():
    assert issubclass(ConfigError, ValueError)


# ---------------------------------------------------------------- preset


def test_presets_validate_without_error():
    DEFAULT_CONFIG.validate()
    PRESET_STUDENT_VN.validate()


# ---------------------------------------------------------------- luật 1–11


def test_rule_1_invalid_timezone():
    config = replace(DEFAULT_CONFIG, timezone="Not/AZone")
    with pytest.raises(ConfigError, match="timezone"):
        config.validate()


def test_rule_2_day_window_start_after_end():
    windows = full_week()
    windows[0] = DayWindow(time(22, 0), time(7, 0))
    config = replace(DEFAULT_CONFIG, day_windows=windows)
    with pytest.raises(ConfigError, match="day_windows\\[0\\]"):
        config.validate()


def test_rule_3_break_outside_day_window():
    late_break = BreakWindow("late", time(22, 0), time(23, 0), frozenset({0}))
    config = replace(DEFAULT_CONFIG, breaks=(late_break,))
    with pytest.raises(ConfigError, match="late"):
        config.validate()


def test_rule_4_overlapping_breaks_same_day():
    breaks = (
        BreakWindow("lunch", time(12, 0), time(13, 0), frozenset({0})),
        BreakWindow("siesta", time(12, 30), time(13, 30), frozenset({0})),
    )
    config = replace(DEFAULT_CONFIG, breaks=breaks)
    with pytest.raises(ConfigError, match="chồng lấn"):
        config.validate()


def test_rule_5_block_min_default_max_ordering():
    # min > default
    bad_min = replace(
        DEFAULT_CONFIG, blocks=replace(DEFAULT_CONFIG.blocks, min_minutes=75, default_minutes=60)
    )
    with pytest.raises(ConfigError, match="min_minutes"):
        bad_min.validate()
    # default > max
    bad_max = replace(DEFAULT_CONFIG, blocks=replace(DEFAULT_CONFIG.blocks, default_minutes=95))
    with pytest.raises(ConfigError, match="default_minutes"):
        bad_max.validate()


def test_rule_6_allocation_unit():
    # unit <= 0
    bad_unit = replace(DEFAULT_CONFIG, blocks=replace(DEFAULT_CONFIG.blocks, allocation_unit=0))
    with pytest.raises(ConfigError, match="allocation_unit"):
        bad_unit.validate()
    # min không chia hết cho unit
    bad_mod = replace(DEFAULT_CONFIG, blocks=replace(DEFAULT_CONFIG.blocks, allocation_unit=20))
    with pytest.raises(ConfigError, match="chia hết"):
        bad_mod.validate()


def test_rule_7_leisure_min_greater_than_minutes():
    leisure = LeisureRule(minutes=60, min_minutes=90, days=frozenset({0}))
    config = replace(DEFAULT_CONFIG, leisure=leisure)
    with pytest.raises(ConfigError, match="leisure.min_minutes"):
        config.validate()


def test_rule_8_leisure_larger_than_day_window():
    leisure = LeisureRule(minutes=1000, min_minutes=0, days=frozenset({0}))
    config = replace(DEFAULT_CONFIG, leisure=leisure)
    with pytest.raises(ConfigError, match="leisure.minutes"):
        config.validate()


def test_rule_9_negative_travel():
    travel = replace(DEFAULT_CONFIG.travel, pre_minutes=-1)
    config = replace(DEFAULT_CONFIG, travel=travel)
    with pytest.raises(ConfigError, match="travel.pre_minutes"):
        config.validate()


def test_rule_10_ceiling_busy_above_free():
    ceiling = replace(DEFAULT_CONFIG.ceiling, ceiling_busy=400)
    config = replace(DEFAULT_CONFIG, ceiling=ceiling)
    with pytest.raises(ConfigError, match="ceiling_busy"):
        config.validate()


def test_rule_11_min_fragment_above_block_min():
    config = replace(DEFAULT_CONFIG, min_fragment_minutes=45)
    with pytest.raises(ConfigError, match="min_fragment_minutes"):
        config.validate()


# ---------------------------------------------------------------- ngày thiếu window


def test_missing_sunday_window_is_accessible_and_valid():
    # day_windows thiếu Chủ Nhật: construct + validate + truy cập đều không raise.
    # Break "lunch" áp dụng mọi ngày nhưng ngày 6 không có window → bỏ qua kiểm containment.
    windows = {day: DayWindow(time(7, 0), time(22, 0)) for day in range(6)}
    config = replace(DEFAULT_CONFIG, day_windows=windows)
    config.validate()  # không raise
    assert 6 not in config.day_windows
    assert config.day_windows[5] == DayWindow(time(7, 0), time(22, 0))


# ---------------------------------------------------------------- luật 12: chain gap


def test_31_chain_gap_equal_pre_plus_post_raises():
    # chain_gap_threshold == pre + post → raise
    travel = replace(DEFAULT_CONFIG.travel, pre_minutes=30, post_minutes=30, chain_gap_threshold=60)
    config = replace(DEFAULT_CONFIG, travel=travel)
    with pytest.raises(ConfigError, match="chain_gap_threshold"):
        config.validate()


def test_32_chain_gap_greater_than_pre_plus_post_ok():
    travel = replace(DEFAULT_CONFIG.travel, pre_minutes=30, post_minutes=30, chain_gap_threshold=61)
    config = replace(DEFAULT_CONFIG, travel=travel)
    config.validate()  # không raise


def test_33_preset_student_vn_validates():
    # Sau khi đổi chain_gap_threshold = 210 (> 120 + 75 = 195)
    PRESET_STUDENT_VN.validate()  # không raise


def test_34_preset_chain_distinguishes_gap_below_vs_above_threshold():
    """Với PRESET mới (threshold=210), gap 180' < 210 → chuỗi; gap 240' > 210 → không chuỗi.
    Kết quả free_intervals khác nhau — test mà part 1B không viết được vì preset cũ vô hiệu hoá luật chuỗi."""
    from datetime import datetime, timezone
    from scheduler_core.capacity import compute_day_capacity
    from scheduler_core.models import FixedEvent

    tz = ZoneInfo(PRESET_STUDENT_VN.timezone)
    day = date(2026, 1, 5)  # Monday

    def mk_event(sh, sm, eh, em):
        return FixedEvent(
            title="e",
            start=datetime.combine(day, time(sh, sm), tzinfo=tz),
            end=datetime.combine(day, time(eh, em), tzinfo=tz),
            requires_travel=True,
        )

    # Gap 180' < 210 → chuỗi → gap bị trừ trọn
    cap_chain = compute_day_capacity(day, [mk_event(8, 0, 9, 0), mk_event(12, 0, 13, 0)], PRESET_STUDENT_VN)
    # Gap 240' > 210 → không chuỗi → mỗi event có đệm riêng
    cap_no_chain = compute_day_capacity(day, [mk_event(8, 0, 9, 0), mk_event(13, 0, 14, 0)], PRESET_STUDENT_VN)
    assert cap_chain.free_intervals != cap_no_chain.free_intervals
