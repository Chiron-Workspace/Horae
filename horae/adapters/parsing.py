"""Phân loại RawTask và trích thời lượng. Hàm thuần, không I/O."""

from __future__ import annotations

import re
import warnings
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Literal, Sequence
from zoneinfo import ZoneInfo

from horae.settings import DEFAULT_ESTIMATE_MINUTES, DEFAULT_ONGOING_MINUTES
from scheduler_core.config import SchedulerConfig
from scheduler_core.models import Assignment, OngoingTask
from horae.adapters.protocols import RawTask


# ---------------------------------------------------------------- result


@dataclass(frozen=True)
class ParseResult:
    """Kết quả phân loại một lô RawTask."""

    assignments: tuple[Assignment, ...]
    ongoing: tuple[OngoingTask, ...]
    skipped: tuple[str, ...]  # task_id bị bỏ qua (label @event)
    warnings: tuple[str, ...]


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


# ---------------------------------------------------------------- public


def parse_tasks(
    raw_tasks: Sequence[RawTask], config: SchedulerConfig
) -> ParseResult:
    """Phân loại RawTask → Assignment / OngoingTask / skipped."""
    tz = ZoneInfo(config.timezone)
    assignments: list[Assignment] = []
    ongoing: list[OngoingTask] = []
    skipped: list[str] = []
    warns: list[str] = []

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
            continue

        # 3. Còn lại, có due date → Assignment
        if raw.due is None:
            # Không có due date → không tạo Assignment (Assignment cần deadline)
            warns.append(
                f"task {raw.task_id}: có [Nm] nhưng không có due date → không tạo Assignment"
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

    return ParseResult(
        assignments=tuple(assignments),
        ongoing=tuple(ongoing),
        skipped=tuple(skipped),
        warnings=tuple(warns),
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
