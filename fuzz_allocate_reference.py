"""Tài liệu tham chiếu: script fuzz test đã dùng để verify Bug 1 trong review
'claude/review-horae-deep-2026-09-05.md'. Đây KHÔNG phải bản vá — chỉ là oracle
để Agent B chạy lại sau khi sửa `_place_min_blocks`/`_cleanup_min`, xác nhận
0 counterexample trước khi coi là xong.

Cách chạy trong repo Horae thật:
    <path-to-repo>/.venv/bin/python fuzz_allocate_reference.py

(script tự thêm root repo vào sys.path — sửa biến REPO_ROOT nếu cần)
"""
import random
import sys

REPO_ROOT = "."  # chạy từ thư mục gốc repo Horae, hoặc sửa thành đường dẫn tuyệt đối
sys.path.insert(0, REPO_ROOT)

from scheduler_core.allocate import _allocate_branch_a

random.seed(12345)


def check_invariants(minutes, fc, min_minutes, label, extra=""):
    problems = []
    for d, m in minutes.items():
        if m > fc[d] + 1e-9:
            problems.append(f"OVERFLOW day={d} minutes={m} > fc={fc[d]}")
        if 0 < m < min_minutes:
            problems.append(f"SUBMIN day={d} minutes={m} < min_minutes={min_minutes}")
    if problems:
        print(f"[{label}] FAIL {extra}")
        for p in problems:
            print("   ", p)
        return False
    return True


def main():
    fails = 0
    trials = 20000
    for i in range(trials):
        ndays = random.randint(1, 6)
        days = list(range(ndays))
        fc = {
            d: random.choice([0, 5, 10, 15, 20, 25, 29, 30, 31, 35, 40, 45, 50, 60, 90, 120])
            for d in days
        }
        min_minutes = random.choice([15, 20, 25, 30])
        unit = random.choice([5, 10, 15])
        total = random.randint(0, 400)
        day_fc = [(d, fc[d]) for d in days]
        try:
            minutes = _allocate_branch_a(total, day_fc, unit, min_minutes)
        except Exception as e:
            print(
                f"[branch_a] EXCEPTION trial={i} total={total} fc={fc} "
                f"min={min_minutes} unit={unit}: {e!r}"
            )
            fails += 1
            continue
        ok = check_invariants(
            minutes,
            fc,
            min_minutes,
            "branch_a",
            f"trial={i} total={total} fc={fc} min={min_minutes} unit={unit} -> {minutes}",
        )
        if not ok:
            fails += 1

    print(f"branch_a: {trials} trials, {fails} failing")
    return 0 if fails == 0 else 1


# Regression cố định: 3 counterexample cụ thể đã tìm được trong review
# 2026-09-05, để giữ lại vĩnh viễn dưới dạng test case tường minh (không phụ
# thuộc seed ngẫu nhiên) một khi được chuyển vào tests/test_allocate.py.
KNOWN_REGRESSIONS = [
    # (total, day_fc, unit, min_minutes) -> mọi ngày trong kết quả phải là 0
    # hoặc >= min_minutes, và không vượt fc.
    (12, [(0, 29)], 10, 25),
    (103, [(0, 31), (1, 31), (2, 35), (3, 0), (4, 45)], 15, 30),
    (268, [(0, 20), (1, 90), (2, 40), (3, 120)], 15, 20),
]


def check_known_regressions():
    ok = True
    for total, day_fc, unit, min_minutes in KNOWN_REGRESSIONS:
        fc = {d: c for d, c in day_fc}
        minutes = _allocate_branch_a(total, day_fc, unit, min_minutes)
        if not check_invariants(
            minutes, fc, min_minutes, "regression",
            f"total={total} fc={fc} min={min_minutes} unit={unit} -> {minutes}",
        ):
            ok = False
    return ok


if __name__ == "__main__":
    r1 = check_known_regressions()
    r2 = main()
    sys.exit(0 if (r1 and r2 == 0) else 1)
