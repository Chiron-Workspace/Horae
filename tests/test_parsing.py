"""Test cho horae.adapters.parsing — phân loại RawTask, trích [Nm]."""

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from horae.adapters.parsing import parse_tasks, regex_parse_title
from horae.adapters.protocols import RawTask
from scheduler_core.config import PRESET_STUDENT_VN
from horae.state.store import LocalStore
from tests.fakes import FakeLLMClient

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


# ---------------------------------------------------------------- nối parse_title (llm)


# Các test được thêm ở phần nối LLM này — không nằm trong "test cũ".
_NEW_LLM_TESTS = (
    "test_parsing_llm_none_unchanged_behavior",
    "test_parsing_regex_success_llm_not_called",
    "test_parsing_llm_called_only_when_regex_fails",
    "test_parsing_llm_result_merged_correctly",
    "test_parsing_store_caches_llm_result",
    "test_parsing_llm_without_store_warns",
    "test_parsing_tiny_llm_estimate_rounded_up",
    "test_parsing_title_and_description_estimates_not_rounded",
    "test_parsing_default_estimate_rounded_up_when_below_custom_min",
)


def _legacy_tests():
    """Mọi test có sẵn của file này trước khi nối LLM."""
    import tests.test_parsing as mod

    return [
        (name, fn)
        for name, fn in vars(mod).items()
        if name.startswith("test_") and name not in _NEW_LLM_TESTS and callable(fn)
    ]


def test_parsing_llm_none_unchanged_behavior(monkeypatch):
    """llm=None → hành vi giống hệt trước khi nối.

    Tiêu đề tự do, không có [Nm], description trống → default 60',
    source="default". Và chạy lại TOÀN BỘ test cũ của file này với llm=None
    truyền TƯỜNG MINH (không dựa vào default parameter) để chắc chắn không có
    nhánh code ẩn nào khác biệt.
    """
    tasks = [_task("t1", "Ôn lại chương điện xoay chiều", due=date(2026, 1, 7))]
    r = parse_tasks(tasks, PRESET_STUDENT_VN, llm=None)
    assert r.assignments[0].estimate_minutes == 60
    assert r.assignments[0].title == "Ôn lại chương điện xoay chiều"
    assert r.estimate_sources["t1"] == "default"
    # Giống hệt lời gọi không truyền tham số
    assert r == parse_tasks(tasks, PRESET_STUDENT_VN)

    import tests.test_parsing as mod

    def explicit_none(task_list):
        return parse_tasks(task_list, PRESET_STUDENT_VN, llm=None)

    monkeypatch.setattr(mod, "_parse", explicit_none)
    legacy = _legacy_tests()
    assert len(legacy) >= 16  # 16 test cũ của file này
    for _name, fn in legacy:
        fn()


def test_parsing_regex_success_llm_not_called():
    """Tiêu đề có [Nm] hợp lệ → LLM không bị chạm lần nào."""
    llm = FakeLLMClient(minutes=999)
    r = parse_tasks(
        [_task("t1", "Làm BTVN Vật Lí [180m]", due=date(2026, 1, 7))],
        PRESET_STUDENT_VN,
        llm,
    )
    assert llm.call_count == 0
    assert r.assignments[0].estimate_minutes == 180
    assert r.estimate_sources["t1"] == "title"


def test_parsing_llm_called_only_when_regex_fails():
    """3 task: [Nm] ở tiêu đề / số phút ở description / không có gì.

    Chỉ task thứ 3 mới gọi LLM → call_count == 1.
    """
    llm = FakeLLMClient(minutes=45)
    tasks = [
        _task("t1", "Làm BTVN Vật Lí [180m]", due=date(2026, 1, 7)),
        _task("t2", "Viết báo cáo", desc="Thời gian làm dự kiến: 90 phút", due=date(2026, 1, 7)),
        _task("t3", "Ôn lại chương điện xoay chiều", due=date(2026, 1, 7)),
    ]
    r = parse_tasks(tasks, PRESET_STUDENT_VN, llm)
    assert llm.call_count == 1
    assert r.estimate_sources == {"t1": "title", "t2": "description", "t3": "llm"}
    assert r.assignments[0].estimate_minutes == 180
    assert r.assignments[1].estimate_minutes == 90


def test_parsing_llm_result_merged_correctly():
    """LLM trả 45' → estimate_minutes=45, source="llm" (khác title/description/default)."""
    llm = FakeLLMClient(minutes=45, title="Ôn chương điện xoay chiều", kind="assignment")
    r = parse_tasks(
        [_task("t3", "Ôn lại chương điện xoay chiều", due=date(2026, 1, 7))],
        PRESET_STUDENT_VN,
        llm,
    )
    assert llm.call_count == 1
    a = r.assignments[0]
    assert a.estimate_minutes == 45
    # LLM chỉ đóng góp số phút — tiêu đề giữ nguyên bản gốc (xem NOTES: cache
    # của parse_title không lưu cleaned_title nên dùng nó sẽ lệch giữa hai lần chạy).
    assert a.title == "Ôn lại chương điện xoay chiều"
    row = r.classifications[0]
    assert row.task_id == "t3"
    assert row.source == "llm"
    assert row.source not in ("title", "description", "default")
    assert row.estimate_minutes == 45


def test_parsing_store_caches_llm_result(tmp_path):
    """Hai lần parse_tasks cùng input + cùng store → LLM chỉ bị gọi 1 lần.

    Đây là test 54 nhưng ở tầng parse_tasks: giữa call-site và cache giờ có
    thêm một lớp gọi, nên phải kiểm lại ở đúng tầng người vận hành dùng.
    """
    store = LocalStore(str(tmp_path))
    llm = FakeLLMClient(minutes=45)
    tasks = [_task("t3", "Ôn lại chương điện xoay chiều", due=date(2026, 1, 7))]

    first = parse_tasks(tasks, PRESET_STUDENT_VN, llm, store)
    assert llm.call_count == 1
    assert first.estimate_sources["t3"] == "llm"

    second = parse_tasks(tasks, PRESET_STUDENT_VN, llm, store)
    assert llm.call_count == 1  # lần hai trúng cache, KHÔNG gọi lại
    assert second.estimate_sources["t3"] == "llm"
    # Cùng nội dung → cùng kết quả, kể cả tiêu đề (cache không lưu cleaned_title)
    assert second.assignments[0] == first.assignments[0]
    assert second.classifications == first.classifications

    # Đổi nội dung → hash đổi → hỏi lại LLM
    parse_tasks(
        [_task("t4", "Đọc trước bài hàm số", due=date(2026, 1, 7))],
        PRESET_STUDENT_VN,
        llm,
        store,
    )
    assert llm.call_count == 2


def test_parsing_llm_without_store_warns():
    """Có llm mà không có store → cảnh báo "không được cache" trong report."""
    llm = FakeLLMClient(minutes=45)
    r = parse_tasks(
        [_task("t3", "Ôn lại chương điện xoay chiều", due=date(2026, 1, 7))],
        PRESET_STUDENT_VN,
        llm,
    )
    assert any("KHÔNG được cache" in w for w in r.warnings)
    # Không gọi LLM thì không cảnh báo thừa
    r2 = parse_tasks(
        [_task("t1", "Làm BTVN Vật Lí [180m]", due=date(2026, 1, 7))],
        PRESET_STUDENT_VN,
        llm,
    )
    assert not any("KHÔNG được cache" in w for w in r2.warnings)


# ---------------------------------------------------------------- việc tí hon (quyết định 2026-09-06)


def test_parsing_tiny_llm_estimate_rounded_up():
    """LLM ước lượng 15' cho một task, min_minutes=30 (PRESET_STUDENT_VN) →
    làm tròn lên 30', source vẫn 'llm', có cảnh báo nêu rõ đã làm tròn.

    Đây chính là ca "Dọn bàn" gây plan.ok=False ở dry-run đêm 2/3
    (2026-09-05, 2026-09-06) — Phương án 2 trong quyết định chính sách."""
    llm = FakeLLMClient(minutes=15, title="Dọn bàn")
    r = parse_tasks(
        [_task("t1", "Dọn bàn", due=date(2026, 1, 7))],
        PRESET_STUDENT_VN,
        llm,
    )
    a = r.assignments[0]
    assert a.estimate_minutes == 30  # làm tròn lên đúng min_minutes
    row = r.classifications[0]
    assert row.source == "llm"  # nguồn không đổi, chỉ số phút đổi
    assert any(
        "làm tròn lên" in w and "30" in w for w in r.warnings
    ), r.warnings


def test_parsing_title_and_description_estimates_not_rounded():
    """[Nm]/description do người dùng tự gõ — dù nhỏ hơn min_minutes vẫn
    GIỮ NGUYÊN, không bị hệ thống tự làm tròn. Đây là ý định tường minh."""
    r = parse_tasks(
        [
            _task("t1", "Việc nhỏ [15m]", due=date(2026, 1, 7)),
            _task("t2", "Việc nhỏ khác", desc="Thời gian làm dự kiến: 15 phút",
                  due=date(2026, 1, 7)),
        ],
        PRESET_STUDENT_VN,
    )
    by_id = {a.task_id: a for a in r.assignments}
    assert by_id["t1"].estimate_minutes == 15
    assert by_id["t2"].estimate_minutes == 15
    sources = r.estimate_sources
    assert sources["t1"] == "title"
    assert sources["t2"] == "description"
    assert not any("làm tròn lên" in w for w in r.warnings)


def test_parsing_default_estimate_rounded_up_when_below_custom_min():
    """DEFAULT_ESTIMATE_MINUTES=60 thường >= min_minutes mọi preset hiện có,
    nên nhánh 'default' trong thực tế không cần làm tròn — nhưng cơ chế vẫn
    phải đúng nếu ai đó cấu hình min_minutes > 60. Dùng config tuỳ biến
    (min_minutes=90) để lộ ra nhánh này, không đợi preset thật thay đổi."""
    from dataclasses import replace as _replace

    config = _replace(
        PRESET_STUDENT_VN, blocks=_replace(PRESET_STUDENT_VN.blocks, min_minutes=90)
    )
    r = parse_tasks(
        [_task("t1", "Task tự do không parse được", due=date(2026, 1, 7))],
        config,
    )
    a = r.assignments[0]
    assert a.estimate_minutes == 90  # DEFAULT_ESTIMATE_MINUTES(60) làm tròn lên 90
    assert r.estimate_sources["t1"] == "default"
    assert any("làm tròn lên" in w and "90" in w for w in r.warnings)
