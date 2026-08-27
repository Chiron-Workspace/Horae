"""Dữ liệu thật của người dùng (tz Asia/Ho_Chi_Minh). Dùng lại trong test tích hợp."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from scheduler_core.models import Assignment, FixedEvent, OngoingTask

TZ = ZoneInfo("Asia/Ho_Chi_Minh")
WEEK_MON = date(2026, 1, 5)  # Monday
TODAY = date(2026, 1, 4)     # Sunday — d1 = Monday

TUE = WEEK_MON + timedelta(days=1)
WED = WEEK_MON + timedelta(days=2)
THU = WEEK_MON + timedelta(days=3)
FRI = WEEK_MON + timedelta(days=4)
SAT = WEEK_MON + timedelta(days=5)
SUN = WEEK_MON + timedelta(days=6)


def _ev(day: date, sh: int, sm: int, eh: int, em: int, title: str, offline: bool) -> FixedEvent:
    return FixedEvent(
        title=title,
        start=datetime.combine(day, time(sh, sm), tzinfo=TZ),
        end=datetime.combine(day, time(eh, em), tzinfo=TZ),
        requires_travel=offline,
    )


def events_by_day() -> dict[date, list[FixedEvent]]:
    return {
        WEEK_MON: [
            _ev(WEEK_MON, 19, 0, 19, 30, "Brightchamps", False),
            _ev(WEEK_MON, 20, 0, 21, 0, "Dạy tiếng Anh", False),
            _ev(WEEK_MON, 20, 0, 21, 30, "Học Vật Lí online", False),
        ],
        TUE: [
            _ev(TUE, 14, 0, 17, 0, "Học Toán", True),
            _ev(TUE, 18, 0, 20, 0, "IELTs", True),
        ],
        WED: [
            _ev(WED, 14, 0, 16, 30, "Học Vật Lí", True),
            _ev(WED, 20, 0, 21, 30, "Học Vật Lí online", False),
            _ev(WED, 21, 30, 22, 45, "SAT Math Bootcamp", False),
        ],
        THU: [
            _ev(THU, 20, 0, 21, 0, "Dạy tiếng Anh", False),
        ],
        FRI: [
            _ev(FRI, 15, 45, 17, 15, "Học Hóa", True),
            _ev(FRI, 18, 30, 19, 0, "Brightchamps", False),
            _ev(FRI, 21, 30, 22, 45, "SAT Math Bootcamp", False),
        ],
        SAT: [
            _ev(SAT, 18, 0, 21, 30, "IELTS", True),
        ],
        SUN: [
            _ev(SUN, 18, 0, 21, 0, "Nghiên cứu khoa học", False),
        ],
    }


def assignments(*, vatli_done: int = 0, hoa_done: int = 0) -> list[Assignment]:
    return [
        Assignment(
            task_id="btvn_vatli",
            title="Làm BTVN Vật Lí",
            estimate_minutes=180,
            deadline=datetime.combine(WED, time(21, 0), tzinfo=TZ),
            done_minutes=vatli_done,
        ),
        Assignment(
            task_id="btvn_hoa",
            title="Làm BTVN Hoá",
            estimate_minutes=60,
            deadline=datetime.combine(FRI, time(21, 0), tzinfo=TZ),
            done_minutes=hoa_done,
        ),
    ]


def ongoing() -> list[OngoingTask]:
    return [
        OngoingTask(task_id="on_ielts", title="Ôn IELTS", daily_target_minutes=60),
        OngoingTask(task_id="on_sat", title="Ôn SAT", daily_target_minutes=60),
    ]
