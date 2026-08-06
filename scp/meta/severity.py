"""Severity — nguồn sự thật DUY NHẤT cho mọi severity trong hệ thống.

[Lỗ hổng #4 fix] Antibody dùng "high", Governance check "critical" → dead code.
Fix: cả 2 module import từ đây, gõ tay string = lỗi kiểu.
"""
from enum import Enum


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"
    WARNING = "warning"  # for stubs only


# Aliases for governance compatibility
CRITICAL_SEVERITIES = {Severity.CRITICAL, Severity.HIGH}  # both trigger KILL path


__all__ = ["Severity", "CRITICAL_SEVERITIES"]
