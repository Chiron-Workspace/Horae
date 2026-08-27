"""Test cho horae.adapters.todoist — dùng FakeHttp + fixture JSON."""

import json
from pathlib import Path

import pytest

from horae.adapters.todoist import TodoistSource, _parse_todoist_due
from horae.adapters.protocols import RawTask
from tests.fakes import FakeHttp

FIX = Path(__file__).parent / "fixtures"


def _load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def test_todoist_fetch_open_tasks():
    """Fetch từ fixture, lọc checked, parse due."""
    fake = FakeHttp()
    fake.add("sync", (200, _load("todoist_items.json")))
    source = TodoistSource(token_env="TODOIST_TOKEN", fetch=fake)
    # Giả lập env
    import os
    os.environ["TODOIST_TOKEN"] = "fake-token"
    tasks = source.fetch_open_tasks()
    assert len(tasks) == 5  # tất cả unchecked trong fixture
    # Task có [180m]
    t1 = next(t for t in tasks if t.task_id == "1001")
    assert "[180m]" in t1.title
    assert t1.due is not None
    # Task @event
    t3 = next(t for t in tasks if t.task_id == "1003")
    assert "event" in t3.labels


def test_parse_todoist_due_date_only():
    due = {"date": "2026-01-07"}
    result = _parse_todoist_due(due)
    assert result is not None
    from datetime import date
    assert result == date(2026, 1, 7)


def test_parse_todoist_due_with_time():
    due = {"date": "2026-01-07T14:00:00"}
    result = _parse_todoist_due(due)
    from datetime import datetime
    assert isinstance(result, datetime)
    assert result.hour == 14


def test_parse_todoist_due_none():
    assert _parse_todoist_due(None) is None
    assert _parse_todoist_due({}) is None
