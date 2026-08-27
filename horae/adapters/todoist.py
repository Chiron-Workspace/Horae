"""Adapter Todoist. Đọc token từ biến môi trường, không gọi mạng ngoài hàm fetch."""

from __future__ import annotations

import json
import os
from typing import Any, Callable, Protocol, Sequence

from horae.adapters.protocols import RawTask


class HttpFetch(Protocol):
    """Hàm GET tới API. Trả (status, body_json_or_text). Fake thay thế được."""

    def __call__(self, url: str, headers: dict[str, str]) -> tuple[int, Any]: ...


def _default_fetch(url: str, headers: dict[str, str]) -> tuple[int, Any]:
    """Default dùng urllib — chỉ import khi thật sự gọi (không kéo vào test)."""
    import urllib.request

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
            return (resp.status, json.loads(body))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            parsed = body
        return (exc.code, parsed)


class TodoistSource:
    """Nguồn task từ Todoist Sync API."""

    BASE_URL = "https://api.todoist.com/sync/v9"

    def __init__(self, token_env: str = "TODOIST_TOKEN", *, fetch: HttpFetch | None = None):
        self._token_env = token_env
        self._fetch: HttpFetch = fetch if fetch is not None else _default_fetch

    def _headers(self) -> dict[str, str]:
        token = os.environ.get(self._token_env)
        if not token:
            raise RuntimeError(f"Biến môi trường {self._token_env} không tồn tại hoặc rỗng")
        return {"Authorization": f"Bearer {token}"}

    def fetch_open_tasks(self) -> Sequence[RawTask]:
        """Lấy tất cả active item, lọc task chưa hoàn thành."""
        url = f"{self.BASE_URL}/sync"
        headers = self._headers()
        headers["Content-Type"] = "application/json"
        # Sync resource: items only
        body = json.dumps({"resource_types": ["items"], "sync_token": "*"})
        # Dùng POST cho sync API
        status, data = self._post(url, headers, body)
        if status != 200:
            raise RuntimeError(f"Todoist sync failed: status={status}, body={data}")
        items = data.get("items", []) if isinstance(data, dict) else []
        tasks: list[RawTask] = []
        for item in items:
            if item.get("checked", False):
                continue
            due = _parse_todoist_due(item.get("due"))
            tasks.append(
                RawTask(
                    task_id=str(item.get("id", "")),
                    title=item.get("content", ""),
                    description=item.get("description", "") or "",
                    labels=tuple(item.get("labels", []) or []),
                    due=due,
                )
            )
        return tuple(tasks)

    def _post(self, url: str, headers: dict[str, str], body: str) -> tuple[int, Any]:
        """POST qua fetch default hoặc fake (fake thường nhận GET nên wrap)."""
        if self._fetch is _default_fetch:
            import urllib.request

            req = urllib.request.Request(url, data=body.encode("utf-8"), headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    return (resp.status, json.loads(resp.read().decode("utf-8")))
            except urllib.error.HTTPError as exc:
                b = exc.read().decode("utf-8")
                try:
                    return (exc.code, json.loads(b))
                except json.JSONDecodeError:
                    return (exc.code, b)
        # Fake path: gọi fetch trực tiếp (test tự xử lý)
        return self._fetch(url, headers)


def _parse_todoist_due(due_obj: dict | None) -> "datetime | date | None":
    """Todoist due: {"date": "2026-01-07", "datetime": "2026-01-07T14:00:00", "timezone": ...}."""
    from datetime import datetime, date

    if not due_obj:
        return None
    date_str = due_obj.get("date")
    if not date_str:
        return None
    # Có giờ?
    if "T" in date_str or " " in date_str:
        try:
            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            return dt
        except ValueError:
            pass
    try:
        return date.fromisoformat(date_str)
    except ValueError:
        return None
