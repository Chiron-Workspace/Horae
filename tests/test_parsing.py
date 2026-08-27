"""Test cho horae.adapters.parsing — phân loại RawTask, trích [Nm]."""

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from horae.adapters.parsing import parse_tasks, regex_parse_title
from horae.adapters.protocols import RawTask
from scheduler_core.config import PRESET_STUDENT_VN

TZ = ZoneInfo(PRESET_STUDENT_VN.timezone)


def _task(tid, title, *, desc="", labels=(), due=None):
    return RawTask(task_id=tid, title=title, description=desc, labels=labels, due=due)


def _parse(tasks):
    return parse_tasks(tasks, PRESET_STUDENT_VN)


# ---------------------------------------------------------------- [Nm] trong tiêu đề


def test_1_title_nmin():
    r = _parse([_task("t1", "Làm BTVN Vật Lí [180m]", due=date(2026, 1, 7))])
    assert len(r.assignments) == 1
    a = r.assignments[0]
    assert a.estimate_minutes == 180
    assert a.title == "Làm BTVN Vật Lí"  # sạch
    assert a.task_id == "t1"


def test_2_nmin_at_start():
    r = _parse([_task("t1", "[45m] Ôn tập", due=date(2026, 1, 7))])
    assert r.assignments[0].estimate_minutes == 45
    assert r.assignments[0].title == "Ôn tập"


def test_3_desc_minutes():
    r = _parse([_task("t1", "Làm BTVN", desc="Thời gian làm dự kiến: 180 phút", due=date(2026, 1, 7))])
    assert r.assignments[0].estimate_minutes == 180


def test_4_no_minutes_default():
    r = _parse([_task("t1", "Làm BTVN", due=date(2026, 1, 7))])
    assert r.assignments[0].estimate_minutes == 60  # mặc định


# ---------------------------------------------------------------- @ontap


def test_5_ontap_with_nmin_day():
    r = _parse([_task("t1", "Ôn IELTS [60m/ngày]", labels=("ontap",))])
    assert len(r.ongoing) == 1
    o = r.ongoing[0]
    assert o.daily_target_minutes == 60
    assert o.title == "Ôn IELTS"


def test_6_ontap_no_nmin_day():
    r = _parse([_task("t1", "Ôn IELTS", labels=("ontap",))])
    assert r.ongoing[0].daily_target_minutes == 60  # mặc định


# ---------------------------------------------------------------- @event


def test_7_event_skipped():
    r = _parse([_task("t1", "Team meeting", labels=("event",))])
    assert "t1" in r.skipped
    assert len(r.assignments) == 0
    assert len(r.ongoing) == 0


def test_8_event_with_nmin_still_skipped():
    r = _parse([_task("t1", "Team meeting [60m]", labels=("event",))])
    assert "t1" in r.skipped
    assert len(r.assignments) == 0


# ---------------------------------------------------------------- deadline


def test_9_no_due_no_assignment():
    r = _parse([_task("t1", "Task [60m]")])  # không due
    assert len(r.assignments) == 0
    assert any("không có due date" in w for w in r.warnings)


def test_10_due_date_only_uses_2100():
    r = _parse([_task("t1", "Task [60m]", due=date(2026, 1, 7))])
    a = r.assignments[0]
    assert a.deadline == datetime.combine(date(2026, 1, 7), time(21, 0), tzinfo=TZ)


def test_11_due_with_time_kept():
    r = _parse([_task("t1", "Task [60m]", due=datetime(2026, 1, 7, 14, 0, tzinfo=TZ))])
    a = r.assignments[0]
    assert a.deadline.hour == 14
    assert a.deadline.tzinfo is not None


# ---------------------------------------------------------------- edge


def test_12_zero_or_negative_nmin():
    r = _parse([_task("t1", "Task [0m]", due=date(2026, 1, 7))])
    assert r.assignments[0].estimate_minutes == 60  # fallback mặc định
    r2 = _parse([_task("t1", "Task [-5m]", due=date(2026, 1, 7))])
    # [-5m] — regex \d+ không match dấu âm → không parse → default
    assert r2.assignments[0].estimate_minutes == 60


def test_13_space_and_uppercase():
    r = _parse([_task("t1", "Task [90 m]", due=date(2026, 1, 7))])
    assert r.assignments[0].estimate_minutes == 90
    r2 = _parse([_task("t1", "Task [90M]", due=date(2026, 1, 7))])
    assert r2.assignments[0].estimate_minutes == 90


def test_14_two_nmin_takes_first():
    r = _parse([_task("t1", "[60m] X [90m]", due=date(2026, 1, 7))])
    assert r.assignments[0].estimate_minutes == 60
    assert any("nhiều" in w for w in r.warnings)


# ---------------------------------------------------------------- regex_parse_title (cho 2B)


def test_regex_parse_title_match():
    result = regex_parse_title("Làm BTVN Vật Lí [180m]")
    assert result is not None
    assert result[0] == 180
    assert result[1] == "title"
    assert result[2] == "Làm BTVN Vật Lí"


def test_regex_parse_title_no_match():
    assert regex_parse_title("Task tự do không có cú pháp") is None
