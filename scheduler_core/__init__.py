"""scheduler_core — lõi xếp lịch thuần hàm (không I/O, không mạng, không đồng hồ hệ thống)."""

from scheduler_core.allocate import (
    AllocationResult,
    allocate_assignments,
    allocate_ongoing,
)
from scheduler_core.blocks import cut_blocks
from scheduler_core.capacity import compute_day_capacity
from scheduler_core.config import (
    DEFAULT_CONFIG,
    PRESET_STUDENT_VN,
    BlockRule,
    BreakWindow,
    CeilingRule,
    ConfigError,
    DayWindow,
    LeisureRule,
    SchedulerConfig,
    TravelRule,
)
from scheduler_core.intervals import (
    clip,
    drop_shorter_than,
    find_contiguous_block,
    longest_interval,
    normalize,
    subtract,
    total_minutes,
)
from scheduler_core.models import (
    Assignment,
    Block,
    CheckResult,
    DayCapacity,
    FixedEvent,
    Interval,
    OngoingTask,
)
from scheduler_core.plan import PlanResult, build_plan
from scheduler_core.validate import run_checks
from scheduler_core.verify import compute_leisure, forbidden_intervals, expected_free_intervals

__all__ = [
    "DEFAULT_CONFIG",
    "PRESET_STUDENT_VN",
    "AllocationResult",
    "Assignment",
    "Block",
    "BlockRule",
    "BreakWindow",
    "CeilingRule",
    "CheckResult",
    "ConfigError",
    "DayCapacity",
    "DayWindow",
    "FixedEvent",
    "Interval",
    "LeisureRule",
    "OngoingTask",
    "PlanResult",
    "SchedulerConfig",
    "TravelRule",
    "allocate_assignments",
    "allocate_ongoing",
    "build_plan",
    "clip",
    "compute_day_capacity",
    "compute_leisure",
    "cut_blocks",
    "drop_shorter_than",
    "expected_free_intervals",
    "find_contiguous_block",
    "forbidden_intervals",
    "longest_interval",
    "normalize",
    "run_checks",
    "subtract",
    "total_minutes",
]
