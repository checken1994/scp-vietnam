"""
[1] Mutation Testing — đo test quality.

TÁI SAO: 344 tests pass nhưng không biết mạnh hay yếu.
Mutation testing sửa code ngẫu nhiên (thay + thành -, xóa dòng) → chạy test.
Nếu test vẫn PASS sau khi code bị sửa → test YẾU (không phát hiện thay đổi).

Run: mutmut run --paths-to-mutate=scp/runtime/judge.py
     mutmut results  # xem tỷ lệ mutants bị bắt
     mutmut html     # báo cáo HTML

Score > 80% = test mạnh. Score < 50% = test yếu.
"""
import os

# Focus mutation on critical files (not data_sources — too many)
MUTATE_PATHS = [
    "scp/runtime/judge.py",
    "scp/runtime/engine.py",
    "scp/core/db_manager.py",
    "scp/core/data_partitioner.py",
    "scp/meta/governance_v97.py",
    "scp/meta/constitution.py",
    "scp/llm_gateway/client.py",
]
