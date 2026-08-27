"""Lưu trữ cục bộ: config.json + runs/{date}.json. Không database, không lưu tiến độ."""

from __future__ import annotations

import json
import os
from datetime import date
from typing import Any
from zoneinfo import ZoneInfo

from scheduler_core.config import SchedulerConfig


class LocalStore:
    """JSON trên đĩa. Thư mục chưa tồn tại → tự tạo."""

    def __init__(self, base_dir: str):
        self._base_dir = base_dir
        self._runs_dir = os.path.join(base_dir, "runs")
        os.makedirs(self._runs_dir, exist_ok=True)

    def save_config(self, config: SchedulerConfig) -> None:
        path = os.path.join(self._base_dir, "config.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(config.to_dict(), f, ensure_ascii=False, indent=2)

    def load_config(self) -> SchedulerConfig:
        path = os.path.join(self._base_dir, "config.json")
        with open(path, "r", encoding="utf-8") as f:
            return SchedulerConfig.from_dict(json.load(f))

    def save_run(self, day: date, payload: dict[str, Any]) -> None:
        """Ghi log chạy của ngày. Ghi đè nếu đã có."""
        path = os.path.join(self._runs_dir, f"{day.isoformat()}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, default=str)

    def load_run(self, day: date) -> dict[str, Any] | None:
        path = os.path.join(self._runs_dir, f"{day.isoformat()}.json")
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
