"""Test cho horae.state.ledger — sổ tiến độ từ block [Auto] quá khứ."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from horae.adapters.protocols import AutoBlock
from horae.state.ledger import build_ledger

TZ = ZoneInfo("Asia/Ho_Chi_Minh")
D1 = datetime(2026, 1, 5, 0, 0, tzinfo=TZ)  # 00:00 D1
BEFORE = D1


def _block(eid, task_id, start, end, kind="assignment"):
    return AutoBlock(
        event_id=eid, task_id=task_id, title=f"[Auto] [{kind}] {task_id}",
        start=start, end=end, kind=kind, calendar_id="auto-study",
    )


def test_22_two_past_blocks_same_task_summed():
    """Hai block đã qua của cùng task → cộng đúng tổng."""
    blocks = [
        _block("a1", "t1", datetime(2026, 1, 3, 7, 0, tzinfo=TZ), datetime(2026, 1, 3, 8, 0, tzinfo=TZ)),
        _block("a2", "t1", datetime(2026, 1, 3, 9, 0, tzinfo=TZ), datetime(2026, 1, 3, 10, 30, tzinfo=TZ)),
    ]
    ledger = build_ledger(blocks, BEFORE)
    assert ledger == {"t1": 150}  # 60 + 90


def test_23_block_in_d1_not_counted():
    """Block nằm trong D1 → KHÔNG được đếm."""
    blocks = [
        _block("a1", "t1", datetime(2026, 1, 5, 7, 0, tzinfo=TZ), datetime(2026, 1, 5, 8, 0, tzinfo=TZ)),
    ]
    ledger = build_ledger(blocks, BEFORE)
    assert ledger == {}


def test_24_boundary_less_equal():
    """Block kết thúc đúng 00:00 D1 → end == before → `end <= before` → được đếm.
    Block kết thúc 00:01 D1 → end > before → không đếm."""
    # end == before → đếm (<=)
    blocks_eq = [
        _block("a1", "t1", datetime(2026, 1, 4, 23, 0, tzinfo=TZ), datetime(2026, 1, 5, 0, 0, tzinfo=TZ)),
    ]
    assert build_ledger(blocks_eq, BEFORE) == {"t1": 60}
    # end > before → không đếm
    blocks_gt = [
        _block("a2", "t2", datetime(2026, 1, 5, 0, 0, tzinfo=TZ), datetime(2026, 1, 5, 1, 0, tzinfo=TZ)),
    ]
    assert build_ledger(blocks_gt, BEFORE) == {}


def test_25_ongoing_not_in_ledger():
    """Block kind='ongoing' → không vào ledger."""
    blocks = [
        _block("a1", "t1", datetime(2026, 1, 3, 7, 0, tzinfo=TZ), datetime(2026, 1, 3, 8, 0, tzinfo=TZ), kind="ongoing"),
    ]
    ledger = build_ledger(blocks, BEFORE)
    assert ledger == {}


def test_26_missing_task_id_skipped_with_warning():
    """Block thiếu task ID → bỏ qua + cảnh báo, không raise."""
    blocks = [
        AutoBlock(
            event_id="a1", task_id="", title="[Auto] bad",
            start=datetime(2026, 1, 3, 7, 0, tzinfo=TZ),
            end=datetime(2026, 1, 3, 8, 0, tzinfo=TZ),
            kind="assignment", calendar_id="auto-study",
        ),
    ]
    with pytest.warns(UserWarning):
        ledger = build_ledger(blocks, BEFORE)
    assert ledger == {}


def test_27_empty_ledger():
    """Ledger rỗng → trả dict rỗng, không raise."""
    assert build_ledger([], BEFORE) == {}
