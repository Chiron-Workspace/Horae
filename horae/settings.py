"""Hằng số điều chỉnh của lớp adapter (không thuộc scheduler_core)."""

from __future__ import annotations

# Mục tiêu phút/ngày mặc định cho OngoingTask khi tiêu đề không có [Nm/ngày].
DEFAULT_ONGOING_MINUTES: int = 60

# Mặc định ước tính (phút) cho Assignment khi khôngparse được thời lượng.
DEFAULT_ESTIMATE_MINUTES: int = 60

# Tên calendar ghi block [Auto]. Writer chỉ ghi vào calendar tên chính xác này.
AUTO_STUDY_CALENDAR: str = "Auto-Study"

# Danh sách calendar tuyệt đối bỏ qua — không truy vấn, không đọc, không ghi.
IGNORED_CALENDARS: tuple[str, ...] = ("HDT.IE.ADVANCED 32",)

# Tiền tố đánh dấu block tự động trong tiêu đề/sự kiện.
AUTO_BLOCK_PREFIX: str = "[Auto]"

# Dấu hiệu nhận biết block tự động trong description (khớp task ID).
TODOIST_ID_PREFIX: str = "Todoist task ID:"
