"""Phân loại RawTask và trích thời lượng. Hàm thuần, không I/O."""

from __future__ import annotations

import re
import warnings
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import TYPE_CHECKING, Literal, Sequence
from zoneinfo import ZoneInfo

from horae.settings import DEFAULT_ESTIMATE_MINUTES, DEFAULT_ONGOING_MINUTES
from scheduler_core.config import SchedulerConfig
from scheduler_core.models import Assignment, OngoingTask
from horae.adapters.protocols import RawTask

if TYPE_CHECKING:  # chỉ để chú thích kiểu — không import horae.llm lúc chạy
    from horae.llm.registry import LLMClient
    from horae.state.store import LocalStore


# ---------------------------------------------------------------- result


@dataclass(frozen=True)
class TaskClassification:
    """Một dòng của bảng "Todoist — phân loại".

    ``source`` cho biết ước lượng đến từ đâu: ``title`` ([Nm] trong tiêu đề),
    ``description`` (số phút trong mô tả), ``llm`` (parse_title đoán), hay
    ``default`` (không đoán được gì — dùng hằng số mặc định). Người vận hành
    đọc báo cáo dry-run cần phân biệt "60 phút vì LLM đoán vậy" với
    "60 phút vì không đoán được gì cả".
    """

    task_id: str
    title: str
    kind: Literal["assignment", "ongoing"]
    estimate_minutes: int
    source: Literal["title", "description", "llm", "default"]


@dataclass(frozen=True)
class ParseResult:
    """Kết quả phân loại một lô RawTask."""

    assignments: tuple[Assignment, ...]
    ongoing: tuple[OngoingTask, ...]
    skipped: tuple[str, ...]  # task_id bị bỏ qua (label @event)
    warnings: tuple[str, ...]
    classifications: tuple[TaskClassification, ...] = ()

    @property
    def estimate_sources(self) -> dict[str, str]:
        """{task_id: source} — tra nhanh nguồn ước lượng của từng task."""
        return {row.task_id: row.source for row in self.classifications}


# ---------------------------------------------------------------- regex

# [Nm] hoặc [Nm/ngày] — số có thể có khoảng trắng, m/M hoa thường.
_RE_NMIN = re.compile(r"\[\s*(\d+)\s*[mM]\s*\]")
_RE_NMIN_DAY = re.compile(r"\[\s*(\d+)\s*[mM]\s*/\s*ngày\s*\]", re.IGNORECASE)
# Số phút trong description: "180 phút", "est 180m", "thời gian làm dự kiến: 180 phút"
_RE_DESC_MIN = re.compile(r"(?:(?:est|thời\s+gi?(?:an|am)\s+(?:làm\s+)?(?:dự\s+kiến|dk)?:?)\s*)?(\d+)\s*(?:phút|m\b)", re.IGNORECASE)


# ---------------------------------------------------------------- helpers


def _clean_title(title: str, token: str) -> str:
    """Bỏ token [Nm...] khỏi tiêu đề, dọn khoảng trắng thừa."""
    cleaned = title.replace(token, "").strip()
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    # Bỏ dấu ngoặc rỗng còn sót
    cleaned = re.sub(r"\[\s*\]", "", cleaned).strip()
    return cleaned


def _find_first_match(pattern: re.Pattern, text: str) -> tuple[str, int] | None:
    """Trả về (match.group(0), match.start()) của lần match đầu, hoặc None."""
    m = pattern.search(text)
    if m:
        return (m.group(0), m.start())
    return None


def _extract_minutes_from_title(title: str) -> tuple[int, str, str | None]:
    """Trích [Nm] khỏi tiêu đề. Trả (minutes, source, cleaned_title | None khi không có).

    source = "title". cleaned_title = tiêu đề đã bỏ token. Phát hiện hai token → cảnh báo.
    """
    matches = list(_RE_NMIN.finditer(title))
    # Lọc trùng: [Nm/ngày] cũng match _RE_NMIN (vì [Nm/ngày] chứa [Nm) — cần loại.
    # _RE_NMIN không có/ký, nên "[60m/ngày]" → match "[60m]"? Test: pattern \[\s*(\d+)\s*[mM]\s*\]
    # yêu cầu ] sau m (cho phép khoảng trắng). "/ngày" xen giữa → KHÔNG match. An toàn.
    if not matches:
        return (0, "", None)
    first = matches[0]
    minutes = int(first.group(1))
    token = first.group(0)
    cleaned = _clean_title(title, token)
    return (minutes, "title", cleaned)


def _extract_minutes_from_description(description: str) -> int | None:
    """Trích số phút khỏi description. Trả int hoặc None."""
    if not description:
        return None
    m = _RE_DESC_MIN.search(description)
    if m:
        return int(m.group(1))
    return None


def _normalize_labels(labels: Sequence[str]) -> set[str]:
    """Todoist trả label có thể kèm hoặc không kèm @. Bỏ @, lower."""
    return {lbl.lstrip("@").lower() for lbl in labels}


def _estimate_via_llm(
    raw: RawTask, llm: "LLMClient", store: "LocalStore | None"
) -> tuple[int, str, str, list[str]]:
    """Hỏi parse_title khi CẢ HAI luật regex đều thất bại.

    Trả (minutes, source, title, warnings). LLM không cho kết quả dùng được
    (lỗi tạm thời, JSON hỏng, không có provider) → rơi về đúng nhánh mặc định
    cũ, kèm một cảnh báo để báo cáo phân biệt được hai trường hợp.
    ``LLMBadRequestError`` vẫn thoát ra ngoài: đó là lỗi prompt của ta,
    parse_title cố ý không nuốt.

    LLM chỉ đóng góp ``estimate_minutes``. Tiêu đề LUÔN giữ nguyên bản gốc,
    kể cả khi parse_title trả ``cleaned_title`` do LLM dọn: cache của
    parse_title không lưu ``cleaned_title``, nên lần chạy đầu (gọi LLM) và
    lần sau (trúng cache) sẽ cho hai tiêu đề khác nhau cho cùng một task.
    Dùng tiêu đề gốc để hai lần chạy cho ra cùng một kết quả.
    """
    # Import cục bộ: đường đi llm=None không nạp horae.llm, và tránh vòng lặp
    # import (parse_title import ngược lại regex_parse_title của module này).
    from horae.llm.tasks.parse_title import parse_title

    parsed = parse_title(raw.title, llm, description=raw.description, store=store)
    if parsed.source == "llm" and parsed.estimate_minutes > 0:
        return (parsed.estimate_minutes, "llm", raw.title, [])
    return (
        DEFAULT_ESTIMATE_MINUTES,
        "default",
        raw.title,
        [
            f"task {raw.task_id}: LLM không cho ước lượng dùng được, "
            f"dùng mặc định {DEFAULT_ESTIMATE_MINUTES}"
        ],
    )


# ---------------------------------------------------------------- public


def parse_tasks(
    raw_tasks: Sequence[RawTask],
    config: SchedulerConfig,
    llm: "LLMClient | None" = None,
    store: "LocalStore | None" = None,
) -> ParseResult:
    """Phân loại RawTask → Assignment / OngoingTask / skipped.

    ``llm`` mặc định None → hành vi giống hệt trước khi nối LLM: task không
    parse được nhận ``DEFAULT_ESTIMATE_MINUTES`` với ``source="default"``.
    Chỉ khi ``llm is not None`` VÀ tiêu đề không có [Nm] VÀ description không
    có số phút thì parse_title mới được gọi — nó đứng SAU hai luật regex,
    không thay thế chúng.

    ``store`` là cache ước lượng theo nội dung (sha256 của title|description).
    Không có store thì mỗi lần chạy lại hỏi LLM lại cho cùng một task chưa
    đổi nội dung — đúng thứ phi xác định mà cache sinh ra để chặn. Vì vậy
    gọi có ``llm`` mà thiếu ``store`` sẽ sinh cảnh báo trong báo cáo.
    """
    tz = ZoneInfo(config.timezone)
    assignments: list[Assignment] = []
    ongoing: list[OngoingTask] = []
    skipped: list[str] = []
    warns: list[str] = []
    rows: list[TaskClassification] = []
    warned_no_store = False

    for raw in raw_tasks:
        labels = _normalize_labels(raw.labels)

        # 1. label @event → bỏ qua
        if "event" in labels:
            skipped.append(raw.task_id)
            if _RE_NMIN.search(raw.title):
                warns.append(
                    f"task {raw.task_id}: có label @event VÀ [Nm] — label thắng, bị skipped"
                )
            continue

        # 2. label @ontap → OngoingTask
        if "ontap" in labels:
            m = _RE_NMIN_DAY.search(raw.title)
            if m:
                target = int(m.group(1))
                token = m.group(0)
                title = _clean_title(raw.title, token)
            else:
                target = DEFAULT_ONGOING_MINUTES
                title = raw.title
            if target <= 0:
                warns.append(
                    f"task {raw.task_id}: [Nm/ngày] không hợp lệ ({target}), dùng mặc định {DEFAULT_ONGOING_MINUTES}"
                )
                target = DEFAULT_ONGOING_MINUTES
            ongoing.append(
                OngoingTask(task_id=raw.task_id, title=title, daily_target_minutes=target)
            )
            rows.append(
                TaskClassification(
                    task_id=raw.task_id,
                    title=title,
                    kind="ongoing",
                    estimate_minutes=target,
                    source="title" if m else "default",
                )
            )
            continue

        # 3. Còn lại, có due date → Assignment
        if raw.due is None:
            # Không có due date → không tạo Assignment (Assignment cần deadline)
            warns.append(
                f"task {raw.task_id}: không có due date → không tạo Assignment "
                f"(và không hỏi LLM: không có deadline thì không dựng được Assignment)"
            )
            continue

        # Trích thời lượng
        minutes, source, cleaned_title = _extract_minutes_from_title(raw.title)
        if source != "title":
            # Thử description
            desc_min = _extract_minutes_from_description(raw.description)
            if desc_min is not None and desc_min > 0:
                minutes = desc_min
                source = "description"
                cleaned_title = raw.title  # không bóc từ tiêu đề → giữ nguyên
            elif llm is not None:
                # Luật 3, chỉ chạy khi hai luật regex trên đều thất bại.
                if store is None and not warned_no_store:
                    warned_no_store = True
                    warns.append(
                        "LLM được gọi mà không có store: kết quả KHÔNG được cache, "
                        "mỗi lần chạy sẽ hỏi lại LLM cho cùng một task"
                    )
                minutes, source, cleaned_title, llm_warns = _estimate_via_llm(
                    raw, llm, store
                )
                warns.extend(llm_warns)
            else:
                minutes = DEFAULT_ESTIMATE_MINUTES
                source = "default"
                cleaned_title = raw.title
        else:
            # Có [Nm] trong tiêu đề — kiểm hợp lệ & đa token
            if minutes <= 0:
                warns.append(
                    f"task {raw.task_id}: [{minutes}m] không hợp lệ, dùng mặc định {DEFAULT_ESTIMATE_MINUTES}"
                )
                minutes = DEFAULT_ESTIMATE_MINUTES
                source = "default"
                cleaned_title = raw.title
            else:
                all_title_matches = list(_RE_NMIN.finditer(raw.title))
                if len(all_title_matches) > 1:
                    warns.append(
                        f"task {raw.task_id}: tiêu đề có nhiều [Nm], lấy cái đầu tiên"
                    )

        # Deadline
        deadline = _resolve_deadline(raw.due, config, tz)

        assignments.append(
            Assignment(
                task_id=raw.task_id,
                title=cleaned_title,
                estimate_minutes=minutes,
                deadline=deadline,
            )
        )
        rows.append(
            TaskClassification(
                task_id=raw.task_id,
                title=cleaned_title,
                kind="assignment",
                estimate_minutes=minutes,
                source=source,
            )
        )

    return ParseResult(
        assignments=tuple(assignments),
        ongoing=tuple(ongoing),
        skipped=tuple(skipped),
        warnings=tuple(warns),
        classifications=tuple(rows),
    )


def _resolve_deadline(due: datetime | date, config: SchedulerConfig, tz: ZoneInfo) -> datetime:
    """Due có giờ → dùng (quy đổi tz). Chỉ ngày → ngày đó + default_deadline_time tại tz config."""
    if isinstance(due, datetime):
        if due.tzinfo is None:
            due = due.replace(tzinfo=tz)
        return due
    # date → ghép default_deadline_time
    dt = datetime.combine(due, config.default_deadline_time, tzinfo=tz)
    return dt


# ---------------------------------------------------------------- parse_title (cho 2B, nhưng định nghĩa regex dùng chung ở đây)


def regex_parse_title(raw: str) -> tuple[int, str, str] | None:
    """Thử regex trích [Nm] khỏi tiêu đề tự do.

    Trả (minutes, source, cleaned_title) hoặc None khi không match.
    Dùng cho tasks/parse_title.py (2B): regex trước, LLM sau.
    """
    m = _RE_NMIN.search(raw)
    if not m:
        return None
    minutes = int(m.group(1))
    if minutes <= 0:
        return None
    token = m.group(0)
    cleaned = _clean_title(raw, token)
    return (minutes, "title", cleaned)
