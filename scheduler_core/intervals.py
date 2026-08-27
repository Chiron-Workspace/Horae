"""Hàm thuần trên Interval. Vào/ra đều là tuple[Interval, ...] đã sort theo start."""

from __future__ import annotations

from datetime import timedelta
from typing import Iterable, Literal

from scheduler_core.models import Interval

Placement = Literal["latest", "earliest"]


def _sorted(intervals: Iterable[Interval]) -> list[Interval]:
    return sorted(intervals, key=lambda iv: (iv.start, iv.end))


def normalize(intervals: Iterable[Interval]) -> tuple[Interval, ...]:
    """Sort, hợp nhất các khoảng chồng lấn hoặc kề sát (a.end == b.start) thành một."""
    ordered = _sorted(intervals)
    if not ordered:
        return ()
    merged: list[Interval] = [ordered[0]]
    for iv in ordered[1:]:
        last = merged[-1]
        if iv.start <= last.end:  # chồng lấn hoặc kề sát
            if iv.end > last.end:
                merged[-1] = Interval(last.start, iv.end)
        else:
            merged.append(iv)
    return tuple(merged)


def subtract(
    base: Iterable[Interval], cuts: Iterable[Interval]
) -> tuple[Interval, ...]:
    """Trừ cuts khỏi base. Cut nằm giữa một base → base tách làm hai."""
    normalized_cuts = normalize(cuts)
    result: list[Interval] = []
    for b in _sorted(base):
        pieces: list[tuple] = [(b.start, b.end)]
        for cut in normalized_cuts:
            next_pieces: list[tuple] = []
            for start, end in pieces:
                if cut.end <= start or cut.start >= end:
                    next_pieces.append((start, end))
                    continue
                if cut.start > start:
                    next_pieces.append((start, cut.start))
                if cut.end < end:
                    next_pieces.append((cut.end, end))
            pieces = next_pieces
            if not pieces:
                break
        result.extend(Interval(start, end) for start, end in pieces)
    return tuple(result)


def clip(intervals: Iterable[Interval], window: Interval) -> tuple[Interval, ...]:
    """Chỉ giữ phần của các interval nằm trong window."""
    result: list[Interval] = []
    for iv in _sorted(intervals):
        start = max(iv.start, window.start)
        end = min(iv.end, window.end)
        if start < end:
            result.append(Interval(start, end))
    return tuple(result)


def total_minutes(intervals: Iterable[Interval]) -> int:
    """Tổng số phút. Không tự normalize — caller phải normalize trước nếu đầu vào chồng lấn."""
    return sum(iv.duration_minutes for iv in intervals)


def drop_shorter_than(
    intervals: Iterable[Interval], minutes: int
) -> tuple[tuple[Interval, ...], tuple[Interval, ...]]:
    """Trả về (giữ lại, bị loại). Interval đúng `minutes` phút thì được giữ (kiểm biên >=)."""
    kept: list[Interval] = []
    dropped: list[Interval] = []
    for iv in _sorted(intervals):
        (kept if iv.duration_minutes >= minutes else dropped).append(iv)
    return (tuple(kept), tuple(dropped))


def find_contiguous_block(
    intervals: Iterable[Interval], minutes: int, placement: Placement
) -> Interval | None:
    """Tìm một khoảng liên tục dài đúng `minutes` nằm TRỌN trong MỘT interval.

    placement="latest" → muộn nhất có thể (block có start lớn nhất);
    placement="earliest" → sớm nhất có thể (block có start nhỏ nhất).
    Không có interval nào đủ dài → None.
    """
    if minutes <= 0:
        raise ValueError(f"minutes phải > 0, nhận được {minutes}")
    best: Interval | None = None
    for iv in _sorted(intervals):
        if iv.duration_minutes < minutes:
            continue
        if placement == "earliest":
            candidate = Interval(iv.start, iv.start + timedelta(minutes=minutes))
            if best is None or candidate.start < best.start:
                best = candidate
        elif placement == "latest":
            candidate = Interval(iv.end - timedelta(minutes=minutes), iv.end)
            if best is None or candidate.start > best.start:
                best = candidate
        else:
            raise ValueError(
                f"placement phải là 'latest' hoặc 'earliest', nhận được {placement!r}"
            )
    return best


def longest_interval(intervals: Iterable[Interval]) -> Interval | None:
    """Interval dài nhất; rỗng → None."""
    return max(_sorted(intervals), key=lambda iv: iv.duration_minutes, default=None)
