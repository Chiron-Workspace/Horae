"""Common HTTP helper cho providers. Fake-injectable."""

from __future__ import annotations

import json
from typing import Any, Protocol


class HttpPost(Protocol):
    """POST tới API. Trả (status, body_json). Fake thay thế được."""

    def __call__(self, url: str, headers: dict[str, str], body: str) -> tuple[int, Any]: ...


