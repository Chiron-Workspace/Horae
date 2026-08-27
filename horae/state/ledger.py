"""Sổ tiến độ: cộng phút block [Auto] quá khứ theo task_id. Calendar là nguồn sự thật."""

from __future__ import annotations

import warnings
from datetime import datetime
from typing import Sequence

from horae.adapters.protocols import AutoBlock


def _duration_minutes(start: datetime, end: datetime) -> int:
    return int((end - start).total_seconds() // 60)


def build_ledger(auto_blocks: Sequence[AutoBlock], before: datetime) -> dict[str, int]:
    """Cộng số phút các block [Auto] kind='assignment' KẾT THÚC TRƯỚC `before`,
    nhóm theo task_id.

    Luật:
    - Chỉ đếm block kết thúc trước `before` (strict `<`). `before` thường = 00:00 D1.
    - Chỉ đếm kind="assignment". Ongoing không tích lũy.
    - Block thiếu task_id → bỏ qua + cảnh báo.
    """
    ledger: dict[str, int] = {}
    for block in auto_blocks:
        if block.kind != "assignment":
            continue
        if not block.task_id:
            warnings.warn(f"AutoBlock {block.event_id} thiếu task_id, bỏ qua")
            continue
        if block.end > before:
            continue
        minutes = _duration_minutes(block.start, block.end)
        ledger[block.task_id] = ledger.get(block.task_id, 0) + minutes
    return ledger
