# Giúp pytest thêm thư mục gốc của project vào sys.path
# để `import scheduler_core` và `import horae` hoạt động khi chạy `pytest` từ root.
import os

# Token giả cho test adapter (không gọi mạng thật)
os.environ.setdefault("GCAL_TOKEN", "fake-gcal-token")
os.environ.setdefault("TODOIST_TOKEN", "fake-todoist-token")
