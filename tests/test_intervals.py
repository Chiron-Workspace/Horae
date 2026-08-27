"""Test cho scheduler_core.intervals và Interval model."""

from datetime import datetime, timezone

import pytest

from scheduler_core.intervals import (
    clip,
    drop_shorter_than,
    find_contiguous_block,
    longest_interval,
    normalize,
    subtract,
    total_minutes,
)
from scheduler_core.models import Interval

UTC = timezone.utc


def iv(start_h, start_m, end_h, end_m, day=5):
    return Interval(
        datetime(2026, 1, day, start_h, start_m, tzinfo=UTC),
        datetime(2026, 1, day, end_h, end_m, tzinfo=UTC),
    )


# ---------------------------------------------------------------- subtract


def test_subtract_cut_overlaps_head():
    base = (iv(10, 0, 12, 0),)
    cuts = (iv(9, 0, 11, 0),)
    assert subtract(base, cuts) == (iv(11, 0, 12, 0),)


def test_subtract_cut_overlaps_tail():
    base = (iv(10, 0, 12, 0),)
    cuts = (iv(11, 0, 13, 0),)
    assert subtract(base, cuts) == (iv(10, 0, 11, 0),)


def test_subtract_cut_covers_whole_base():
    base = (iv(10, 0, 12, 0),)
    cuts = (iv(9, 0, 13, 0),)
    assert subtract(base, cuts) == ()


def test_subtract_cut_in_middle_splits_base():
    base = (iv(10, 0, 12, 0),)
    cuts = (iv(10, 30, 11, 30),)
    assert subtract(base, cuts) == (iv(10, 0, 10, 30), iv(11, 30, 12, 0))


def test_subtract_cut_disjoint():
    base = (iv(10, 0, 12, 0),)
    cuts = (iv(13, 0, 14, 0),)
    assert subtract(base, cuts) == (iv(10, 0, 12, 0),)


# ---------------------------------------------------------------- normalize


def test_normalize_merges_overlapping():
    result = normalize((iv(10, 0, 11, 0), iv(10, 30, 11, 30)))
    assert result == (iv(10, 0, 11, 30),)


def test_normalize_merges_adjacent():
    result = normalize((iv(10, 0, 11, 0), iv(11, 0, 12, 0)))
    assert result == (iv(10, 0, 12, 0),)


def test_normalize_keeps_disjoint_separate():
    result = normalize((iv(12, 0, 13, 0), iv(10, 0, 11, 0)))
    assert result == (iv(10, 0, 11, 0), iv(12, 0, 13, 0))


def test_normalize_then_total_minutes_counts_overlap_once():
    # 20:00–21:00 và 20:00–21:30 chồng nhau hoàn toàn một phần → 90 phút, không phải 150
    intervals = normalize((iv(20, 0, 21, 0), iv(20, 0, 21, 30)))
    assert total_minutes(intervals) == 90


# ---------------------------------------------------------------- clip


def test_clip_cuts_to_window_boundaries():
    window = iv(9, 0, 12, 0)
    intervals = (
        iv(8, 0, 10, 0),  # vượt biên đầu
        iv(10, 0, 11, 0),  # nằm trọn
        iv(11, 0, 13, 0),  # vượt biên cuối
        iv(13, 0, 14, 0),  # nằm ngoài hoàn toàn → bị loại
    )
    assert clip(intervals, window) == (
        iv(9, 0, 10, 0),
        iv(10, 0, 11, 0),
        iv(11, 0, 12, 0),
    )


# ---------------------------------------------------------------- drop_shorter_than


def test_drop_shorter_than_boundary_inclusive():
    intervals = (iv(10, 0, 10, 15), iv(11, 0, 11, 30), iv(12, 0, 12, 45))
    kept, dropped = drop_shorter_than(intervals, 30)
    assert kept == (iv(11, 0, 11, 30), iv(12, 0, 12, 45))  # đúng 30' được giữ
    assert dropped == (iv(10, 0, 10, 15),)


# ---------------------------------------------------------------- find_contiguous_block


def test_find_contiguous_block_latest_vs_earliest():
    intervals = (iv(7, 0, 12, 0), iv(14, 0, 22, 0))
    earliest = find_contiguous_block(intervals, 180, "earliest")
    latest = find_contiguous_block(intervals, 180, "latest")
    assert earliest == iv(7, 0, 10, 0)
    assert latest == iv(19, 0, 22, 0)
    assert earliest != latest


def test_find_contiguous_block_none_long_enough():
    intervals = (iv(10, 0, 11, 0), iv(13, 0, 14, 30))
    assert find_contiguous_block(intervals, 180, "latest") is None
    assert find_contiguous_block(intervals, 180, "earliest") is None


# ---------------------------------------------------------------- longest_interval


def test_longest_interval():
    assert longest_interval(()) is None
    intervals = (iv(10, 0, 11, 0), iv(13, 0, 15, 30), iv(16, 0, 16, 45))
    assert longest_interval(intervals) == iv(13, 0, 15, 30)


# ---------------------------------------------------------------- Interval model


def test_interval_naive_datetime_raises():
    with pytest.raises(ValueError, match="tz-aware"):
        Interval(datetime(2026, 1, 5, 10, 0), datetime(2026, 1, 5, 11, 0))
    # end naive cũng vậy
    with pytest.raises(ValueError, match="tz-aware"):
        Interval(datetime(2026, 1, 5, 10, 0, tzinfo=UTC), datetime(2026, 1, 5, 11, 0))


def test_interval_start_after_end_raises():
    with pytest.raises(ValueError, match="start < end"):
        Interval(datetime(2026, 1, 5, 11, 0, tzinfo=UTC), datetime(2026, 1, 5, 11, 0, tzinfo=UTC))
    with pytest.raises(ValueError, match="start < end"):
        Interval(datetime(2026, 1, 5, 12, 0, tzinfo=UTC), datetime(2026, 1, 5, 11, 0, tzinfo=UTC))
