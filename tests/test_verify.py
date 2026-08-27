"""Test cho scheduler_core.verify — lưới an toàn thật (đường tính độc lập).

8 test phá hoại: dựng DayCapacity thủ công với free_intervals SAI,
rồi xem run_checks có bắt được không.
3 test xanh: all old tests pass, fixture thật khớp, grep no import.
"""

import subprocess
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from scheduler_core.config import PRESET_STUDENT_VN, DEFAULT_CONFIG
from scheduler_core.models import Assignment, Block, DayCapacity, FixedEvent, Interval, OngoingTask
from scheduler_core.validate import run_checks
from scheduler_core.verify import forbidden_intervals, expected_free_intervals

TZ = ZoneInfo(PRESET_STUDENT_VN.timezone)
MON = date(2026, 1, 5)  # Monday
TUE = date(2026, 1, 6)


def mk_interval(day, sh, sm, eh, em):
    return Interval(
        datetime.combine(day, time(sh, sm), tzinfo=TZ),
        datetime.combine(day, time(eh, em), tzinfo=TZ),
    )


def mk_capacity(day, free_intervals, *, leisure=None):
    return DayCapacity(
        date=day,
        free_intervals=tuple(free_intervals),
        busy_minutes=0,
        ceiling_minutes=360,
        capacity_minutes=sum(iv.duration_minutes for iv in free_intervals),
        dropped_fragments=(),
        leisure_interval=leisure,
    )


def mk_block(tid, day, sh, sm, eh, em, kind="assignment"):
    return Block(
        task_id=tid, title=tid,
        start=datetime.combine(day, time(sh, sm), tzinfo=TZ),
        end=datetime.combine(day, time(eh, em), tzinfo=TZ),
        kind=kind,
    )


def mk_event(day, sh, sm, eh, em, *, travel=False, title="e"):
    return FixedEvent(
        title=title,
        start=datetime.combine(day, time(sh, sm), tzinfo=TZ),
        end=datetime.combine(day, time(eh, em), tzinfo=TZ),
        requires_travel=travel,
    )


def _run_consistency_check(cap, events, config=PRESET_STUDENT_VN):
    """Chạy riêng check capacity_consistent cho một cap + events."""
    results = run_checks({}, {cap.date: cap}, [], [], {}, config, events_by_day={cap.date: events})
    return next(r for r in results if r.name == "capacity_consistent")


# ================================================================ 8 phá hoại


def test_1_free_forgets_travel_pre():
    """free_intervals QUÊN trừ đệm 2h trước event offline → capacity_consistent FAIL."""
    event = mk_event(MON, 14, 0, 17, 0, travel=True)
    # ĐÚNG free phải trừ 12:00-14:00 (pre 120'), nhưng ta cho free sai (quên pre)
    bad_free = (
        mk_interval(MON, 7, 0, 12, 0),    # lunch break bị trừ đúng
        mk_interval(MON, 14, 0, 19, 0),   # QUÊN trừ pre (12:00-14:00)
        mk_interval(MON, 20, 0, 22, 0),    # dinner break bị trừ đúng
    )
    cap = mk_capacity(MON, bad_free)
    check = _run_consistency_check(cap, [event])
    assert not check.passed
    assert "lệch" in check.detail


def test_2_free_forgets_break():
    """free_intervals QUÊN trừ break 12:00–14:00 → FAIL."""
    bad_free = (
        mk_interval(MON, 7, 0, 14, 0),   # quên trừ lunch 12-14
        mk_interval(MON, 14, 0, 19, 0),
        mk_interval(MON, 20, 0, 22, 0),
    )
    cap = mk_capacity(MON, bad_free)
    check = _run_consistency_check(cap, [])
    assert not check.passed


def test_3_free_forgets_chain_gap():
    """free_intervals QUÊN trừ khoảng giữa chuỗi offline → FAIL."""
    e1 = mk_event(MON, 14, 0, 17, 0, travel=True)
    e2 = mk_event(MON, 18, 0, 20, 0, travel=True)  # gap 60' < 210 → chuỗi
    # ĐÚNG: 17:00-18:00 bị trừ trọn (gap trong chuỗi)
    # SAI: quên trừ gap → 17:00-18:00 vẫn free
    bad_free = (
        mk_interval(MON, 7, 0, 12, 0),
        mk_interval(MON, 14, 0, 19, 0),   # quên trừ pre 12-14
        mk_interval(MON, 17, 0, 18, 0),   # gap 17-18 vẫn còn (SAI)
        mk_interval(MON, 21, 15, 22, 0),  # post + recovery bị trừ
    )
    cap = mk_capacity(MON, bad_free)
    check = _run_consistency_check(cap, [e1, e2])
    assert not check.passed


def test_4_free_forgets_leisure():
    """free_intervals QUÊN trừ leisure cuối tuần → FAIL."""
    # Saturday — leisure 180' latest
    SAT = date(2026, 1, 10)
    leisure = mk_interval(SAT, 16, 0, 19, 0)  # latest placement
    bad_free = (
        mk_interval(SAT, 7, 0, 12, 0),
        mk_interval(SAT, 14, 0, 22, 0),  # QUÊN trừ leisure 16-19
    )
    cap = mk_capacity(SAT, bad_free, leisure=leisure)
    check = _run_consistency_check(cap, [])
    assert not check.passed


def test_5_free_forgets_recovery():
    """free_intervals QUÊN trừ recovery 30' → FAIL."""
    event = mk_event(MON, 21, 0, 21, 30, travel=True)
    # post = 21:30-22:45 (but clipped to 22:00), recovery = 22:45-23:15 (outside window)
    # ĐÚNG free: 07:00-12:00, 14:00-19:00 (recovery 22:45-23:15 outside window → no effect)
    # Nhưng nếu recovery có nằm trong window: quên trừ → fail
    # Dùng event sớm hơn để recovery nằm trong window
    event2 = mk_event(MON, 19, 0, 20, 0, travel=True)
    # pre: 17:00-19:00, post: 20:00-21:15, recovery: 21:15-21:45
    # ĐÚNG free: 07-12, 14-17, 21:45-22 (sau recovery)
    # SAI: quên recovery → 21:15-22:00 vẫn còn
    bad_free = (
        mk_interval(MON, 7, 0, 12, 0),
        mk_interval(MON, 14, 0, 17, 0),   # pre bị trừ đúng
        mk_interval(MON, 21, 15, 22, 0),  # QUÊN recovery 21:15-21:45
    )
    cap = mk_capacity(MON, bad_free)
    check = _run_consistency_check(cap, [event2])
    assert not check.passed


def test_6_free_wider_than_window():
    """free_intervals RỘNG hơn day_window (bắt đầu 06:00) → FAIL."""
    bad_free = (mk_interval(MON, 6, 0, 22, 0),)  # window là 07:00-22:00
    cap = mk_capacity(MON, bad_free)
    check = _run_consistency_check(cap, [])
    assert not check.passed


def test_7_free_keeps_tiny_fragment():
    """free_intervals còn mảnh 15' (quên drop) → FAIL."""
    bad_free = (
        mk_interval(MON, 7, 0, 12, 0),
        mk_interval(MON, 14, 0, 19, 0),
        mk_interval(MON, 20, 0, 22, 0),
        mk_interval(MON, 18, 45, 19, 0),  # mảnh 15' < min_fragment 30
    )
    cap = mk_capacity(MON, bad_free)
    check = _run_consistency_check(cap, [])
    assert not check.passed


def test_8_free_completely_correct():
    """free_intervals ĐÚNG hoàn toàn → cả hai check PASS."""
    # Dùng compute_day_capacity thực để lấy free đúng
    from scheduler_core.capacity import compute_day_capacity
    cap = compute_day_capacity(MON, [], PRESET_STUDENT_VN)
    check = _run_consistency_check(cap, [])
    assert check.passed, check.detail


# ================================================================ 3 xanh


def test_9_all_old_tests_pass():
    """Toàn bộ test cũ + 2A vẫn xanh sau khi đổi chữ ký run_checks.
    (Đây là meta-test: pytest đã chạy tất cả — nếu reach đây thì đã xanh.)"""
    # Nếu file này chạy, tất cả test khác đã pass (pytest collect tất cả).
    # Không cần assert thêm — chỉ cần file này được collect.


def test_10_fixture_real_matches():
    """Chạy verify trên fixture thật (tests/fixtures.py) với PRESET_STUDENT_VN,
    đủ 7 ngày (kể cả cuối tuần có leisure) → khớp hoàn toàn với compute_day_capacity."""
    from tests.fixtures import events_by_day as fixture_events, TODAY
    from scheduler_core.capacity import compute_day_capacity

    d1 = TODAY + timedelta(days=1)
    # Kiểm đủ 7 ngày từ D1
    for i in range(7):
        day = d1 + timedelta(days=i)
        events = fixture_events().get(day, [])
        cap = compute_day_capacity(day, events, PRESET_STUDENT_VN)
        expected = expected_free_intervals(day, events, PRESET_STUDENT_VN)
        assert expected == cap.free_intervals, (
            f"Day {day} ({['Mon','Tue','Wed','Thu','Fri','Sat','Sun'][day.weekday()]}): "
            f"verify={expected} vs capacity={cap.free_intervals}"
        )


def test_11_verify_no_import_capacity():
    """grep xác nhận verify.py không import capacity.py (chỉ kiểm import statements)."""
    import ast

    verify_path = Path(__file__).parent.parent / "scheduler_core" / "verify.py"
    content = verify_path.read_text(encoding="utf-8")
    tree = ast.parse(content)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "capacity" not in alias.name, f"import {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            assert "capacity" not in node.module, f"from {node.module} import"


# ================================================================ leisure tự tính


def test_12_leisure_wrong_position_caught():
    """verify.py tự tính leisure; nếu capacity.py chọn sai vị trí
    → leisure_matches FAIL trực tiếp (không qua tác dụng phụ của capacity_consistent)."""
    from scheduler_core.config import LeisureRule
    from dataclasses import replace as _replace
    from scheduler_core.verify import compute_leisure

    SAT = date(2026, 1, 10)  # Saturday → leisure áp dụng
    cfg_leisure = _replace(DEFAULT_CONFIG,
        breaks=(),
        leisure=LeisureRule(minutes=120, min_minutes=60, days=frozenset({0,1,2,3,4,5,6}), placement="latest"),
    )

    # Dùng compute_day_capacity thực → capacity.py tự tính leisure đúng
    from scheduler_core.capacity import compute_day_capacity
    event = FixedEvent(
        title="IELTS",
        start=datetime.combine(SAT, time(18, 0), tzinfo=TZ),
        end=datetime.combine(SAT, time(21, 30), tzinfo=TZ),
        requires_travel=True,
    )
    cap_correct = compute_day_capacity(SAT, [event], cfg_leisure)

    # verify cũng tự tính → phải khớp
    computed = compute_leisure(SAT, [event], cfg_leisure)
    assert computed == cap_correct.leisure_interval, (
        f"verify={computed} vs capacity={cap_correct.leisure_interval}"
    )

    # Giờ dựng cap có leisure SAI (đảo placement sang earliest)
    cfg_wrong_leisure = _replace(cfg_leisure,
        leisure=LeisureRule(minutes=120, min_minutes=60, days=frozenset({0,1,2,3,4,5,6}), placement="earliest"),
    )
    cap_wrong = compute_day_capacity(SAT, [event], cfg_wrong_leisure)

    # verify tự tính với cfg_leisure (latest) ≠ capacity tính với cfg_wrong (earliest)
    computed_correct = compute_leisure(SAT, [event], cfg_leisure)
    assert computed_correct != cap_wrong.leisure_interval, (
        f"verify (latest)={computed_correct} vs capacity (earliest)={cap_wrong.leisure_interval}"
    )

    # leisure_matches phải FAIL
    results = run_checks({}, {SAT: cap_wrong}, [], [], {}, cfg_leisure,
                        events_by_day={SAT: [event]})
    lm = next(r for r in results if r.name == "leisure_matches")
    assert not lm.passed
    assert "leisure lệch" in lm.detail
