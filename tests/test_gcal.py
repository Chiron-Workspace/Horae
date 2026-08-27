"""Test cho horae.adapters.gcal — dùng FakeHttp + fixture JSON, không gọi mạng."""

import json
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from horae.adapters.gcal import (
    GoogleCalendarReader,
    GoogleCalendarWriter,
    WriterTargetMissing,
    _detect_travel,
    _extract_task_id,
    _is_all_day,
    _parse_gcal_datetime,
)
from horae.settings import AUTO_STUDY_CALENDAR, IGNORED_CALENDARS
from scheduler_core.config import PRESET_STUDENT_VN
from scheduler_core.models import Block
from tests.fakes import FakeHttp

FIX = Path(__file__).parent / "fixtures"
TZ = ZoneInfo(PRESET_STUDENT_VN.timezone)


def _load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def _cal_list_response():
    return _load("gcal_calendar_list.json")


# ---------------------------------------------------------------- transparency


def test_15_missing_transparency_is_opaque():
    """Event thiếu transparency → is_opaque=True."""
    fake = FakeHttp()
    fake.add("calendarList", (200, _cal_list_response()))
    # Chỉ trả event cho calendar 'school', các calendar khác trả rỗng
    def events_handler(url, headers, method, body):
        if "school@group.calendar.google.com" in url:
            return (200, {"items": [
                {
                    "id": "e1", "summary": "Test", "description": "Online",
                    "start": {"dateTime": "2026-01-05T10:00:00+07:00"},
                    "end": {"dateTime": "2026-01-05T11:00:00+07:00"},
                }
            ]})
        return (200, {"items": []})
    fake.add("/events", events_handler)
    reader = GoogleCalendarReader(PRESET_STUDENT_VN, token_env="GCAL_TOKEN", fetch=fake)
    events = reader.list_events(
        datetime(2026, 1, 5, 0, 0, tzinfo=TZ), datetime(2026, 1, 6, 0, 0, tzinfo=TZ)
    )
    assert len(events) == 1
    assert events[0].is_opaque is True


# ---------------------------------------------------------------- all-day


def test_16_all_day_event():
    """Event start.date (không dateTime) → is_all_day=True."""
    dt_obj = {"date": "2026-01-07"}
    assert _is_all_day(dt_obj) is True
    dt_obj2 = {"dateTime": "2026-01-07T14:00:00+07:00"}
    assert _is_all_day(dt_obj2) is False


# ---------------------------------------------------------------- ignored calendars


def test_17_ignored_calendar_not_queried():
    """Calendar 'HDT.IE.ADVANCED 32' không xuất hiện trong request nào."""
    fake = FakeHttp()
    fake.add("calendarList", (200, _cal_list_response()))
    fake.add("/events", (200, {"items": []}))
    reader = GoogleCalendarReader(PRESET_STUDENT_VN, token_env="GCAL_TOKEN", fetch=fake)
    reader.list_events(
        datetime(2026, 1, 5, 0, 0, tzinfo=TZ), datetime(2026, 1, 6, 0, 0, tzinfo=TZ)
    )
    # Kiểm: không request nào chứa id của calendar bị loại
    ignored_id = "primary@group.calendar.google.com"  # id của HDT.IE.ADVANCED 32 trong fixture
    for call in fake.calls:
        assert ignored_id not in call["url"], f"Calendar bị loại vẫn bị truy vấn: {call['url']}"


# ---------------------------------------------------------------- travel detection


def test_18_travel_detection():
    travel, w = _detect_travel("Offline", "e1")
    assert travel is True
    assert len(w) == 0

    travel, w = _detect_travel("Online", "e1")
    assert travel is False
    assert len(w) == 0

    travel, w = _detect_travel("", "e1")
    assert travel is False
    assert len(w) > 0  # cảnh báo


# ---------------------------------------------------------------- writer


def test_19_writer_no_auto_study_raises():
    """Calendar list không có Auto-Study → raise WriterTargetMissing, không create."""
    fake = FakeHttp()
    cal_list_no_auto = {
        "items": [
            {"id": "school@group.calendar.google.com", "summary": "School"},
        ]
    }
    fake.add("calendarList", (200, cal_list_no_auto))
    writer = GoogleCalendarWriter(PRESET_STUDENT_VN, token_env="GCAL_TOKEN", fetch=fake)
    block = Block(
        task_id="t1", title="Test", kind="assignment",
        start=datetime(2026, 1, 5, 7, 0, tzinfo=TZ),
        end=datetime(2026, 1, 5, 8, 0, tzinfo=TZ),
    )
    from horae.adapters.protocols import BlockMeta
    meta = BlockMeta(task_id="t1", source_id="t1")
    with pytest.raises(WriterTargetMissing):
        writer.create_block(block, meta)
    # Không có lệnh create nào (chỉ calendarList)
    create_calls = [c for c in fake.calls if c["method"] == "POST" and "/events" in c["url"]]
    assert len(create_calls) == 0


def test_20_writer_creates_three_blocks():
    """Writer ghi 3 block → đúng 3 lệnh create, tất cả cùng calendar_id Auto-Study."""
    fake = FakeHttp()
    fake.add("calendarList", (200, _cal_list_response()))
    fake.add("/events", (200, {"id": "new-event"}))
    writer = GoogleCalendarWriter(PRESET_STUDENT_VN, token_env="GCAL_TOKEN", fetch=fake)
    from horae.adapters.protocols import BlockMeta
    blocks = [
        Block(task_id=f"t{i}", title=f"Task {i}", kind="assignment",
              start=datetime(2026, 1, 5, 7 + i, 0, tzinfo=TZ),
              end=datetime(2026, 1, 5, 7 + i + 1, 0, tzinfo=TZ))
        for i in range(3)
    ]
    for b in blocks:
        writer.create_block(b, BlockMeta(task_id=b.task_id, source_id=b.task_id))
    create_calls = [c for c in fake.calls if c["method"] == "POST" and "/events" in c["url"]]
    assert len(create_calls) == 3
    auto_study_id = "autostudy@group.calendar.google.com"
    for c in create_calls:
        assert auto_study_id in c["url"]


# ---------------------------------------------------------------- timezone


def test_21_timezone_conversion():
    """Event trả về tz khác → quy đổi đúng về tz config."""
    dt_obj = {"dateTime": "2026-01-05T12:00:00+00:00"}  # UTC noon
    result = _parse_gcal_datetime(dt_obj, TZ)
    # +07:00 → 19:00
    assert result.astimezone(TZ).hour == 19
