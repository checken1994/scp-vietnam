"""
SCP V106 — DoSProtectionEngine
================================
Bảo vệ SCP khỏi DoS attack — đặc biệt attack lợi dụng triết lý SCP.

Vấn đề: Attacker gửi 1000 câu "xám" (OUT_OF_SCOPE) → SCP tự KILL 1000 lần → DoS

Giải pháp 3 lớp:
  1. RATE LIMITING — giới hạn requests/IP/phút
  2. CIRCUIT BREAKER — nếu quá nhiều UNKNOWN/SPECULATIVE liên tiếp → throttle
  3. RESOURCE QUOTA — giới hạn CPU/RAM per request + total

Naming convention: <Purpose>Engine (world standard).
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("scp.security.dos_protection")


@dataclass
class DoSAlert:
    """Cảnh báo DoS."""
    alert_type: str  # rate_limit | circuit_breaker | resource_quota
    severity: str  # warning | critical
    message: str
    ip: str = ""
    current_rate: float = 0.0
    threshold: float = 0.0
    action_taken: str = ""  # throttle | block | circuit_open


class DoSProtectionEngine:
    """Bảo vệ SCP khỏi DoS — rate limiting + circuit breaker + resource quota.

    Naming convention: <Purpose>Engine (world standard).

    3 layers:
      1. Rate Limiting: max 60 requests/phút per IP (1 req/giây)
      2. Circuit Breaker: nếu 10 UNKNOWN/SPECULATIVE liên tiếp → throttle 5 giây
      3. Resource Quota: max 100 concurrent requests, max 10s per request
    """

    # Rate limiting
    MAX_REQUESTS_PER_MINUTE = 60
    MAX_REQUESTS_PER_HOUR = 1000

    # Circuit breaker
    CIRCUIT_UNKNOWN_THRESHOLD = 10  # 10 UNKNOWN liên tiếp → open circuit
    CIRCUIT_RESET_TIME = 30  # 30 giây trước khi thử lại
    CIRCUIT_COOLDOWN = 5  # 5 giây throttle khi circuit open

    # Resource quota
    MAX_CONCURRENT = 100
    MAX_REQUEST_TIME_S = 10

    def __init__(self):
        # Rate limiting per IP
        self._requests_per_minute: dict[str, deque] = defaultdict(lambda: deque(maxlen=120))
        self._requests_per_hour: dict[str, deque] = defaultdict(lambda: deque(maxlen=7200))

        # Circuit breaker
        self._consecutive_unknown: int = 0
        self._circuit_state: str = "closed"  # closed | open | half_open
        self._circuit_opened_at: float = 0
        self._last_throttle: float = 0

        # Resource quota
        self._current_concurrent: int = 0

        # Stats
        self._stats = {
            "total_requests": 0,
            "total_blocked_rate": 0,
            "total_throttled": 0,
            "circuit_opens": 0,
            "max_concurrent_seen": 0,
        }

    def check_request(self, ip: str = "unknown") -> DoSAlert | None:
        """Check if request should be allowed.

        Returns:
            None if allowed, DoSAlert if blocked/throttled.
        """
        self._stats["total_requests"] += 1
        now = time.time()

        # === LAYER 1: RATE LIMITING ===
        # Per-minute
        minute_requests = self._requests_per_minute[ip]
        # Remove old entries (> 60 seconds)
        while minute_requests and now - minute_requests[0] > 60:
            minute_requests.popleft()
        if len(minute_requests) >= self.MAX_REQUESTS_PER_MINUTE:
            self._stats["total_blocked_rate"] += 1
            return DoSAlert(
                alert_type="rate_limit",
                severity="critical",
                message=f"Rate limit exceeded: {len(minute_requests)} req/min for {ip}",
                ip=ip,
                current_rate=float(len(minute_requests)),
                threshold=float(self.MAX_REQUESTS_PER_MINUTE),
                action_taken="block",
            )

        # Per-hour
        hour_requests = self._requests_per_hour[ip]
        while hour_requests and now - hour_requests[0] > 3600:
            hour_requests.popleft()
        if len(hour_requests) >= self.MAX_REQUESTS_PER_HOUR:
            self._stats["total_blocked_rate"] += 1
            return DoSAlert(
                alert_type="rate_limit",
                severity="critical",
                message=f"Hourly limit exceeded: {len(hour_requests)} req/hour for {ip}",
                ip=ip,
                current_rate=float(len(hour_requests)),
                threshold=float(self.MAX_REQUESTS_PER_HOUR),
                action_taken="block",
            )

        # Record request
        minute_requests.append(now)
        hour_requests.append(now)

        # === LAYER 2: CIRCUIT BREAKER ===
        if self._circuit_state == "open":
            if now - self._circuit_opened_at > self.CIRCUIT_RESET_TIME:
                self._circuit_state = "half_open"
                logger.info("[DoSProtection] Circuit → half_open (trying again)")
            else:
                # Still open — throttle
                if now - self._last_throttle < self.CIRCUIT_COOLDOWN:
                    self._stats["total_throttled"] += 1
                    return DoSAlert(
                        alert_type="circuit_breaker",
                        severity="warning",
                        message=f"Circuit OPEN — throttling for {self.CIRCUIT_COOLDOWN}s",
                        action_taken="throttle",
                    )

        # === LAYER 3: RESOURCE QUOTA ===
        if self._current_concurrent >= self.MAX_CONCURRENT:
            return DoSAlert(
                alert_type="resource_quota",
                severity="critical",
                message=f"Max concurrent reached: {self._current_concurrent}/{self.MAX_CONCURRENT}",
                current_rate=float(self._current_concurrent),
                threshold=float(self.MAX_CONCURRENT),
                action_taken="block",
            )

        self._current_concurrent += 1
        self._stats["max_concurrent_seen"] = max(self._stats["max_concurrent_seen"], self._current_concurrent)

        return None  # Request allowed

    def record_verdict(self, verdict: str):
        """Record verdict result — update circuit breaker."""
        self._current_concurrent = max(0, self._current_concurrent - 1)

        # Circuit breaker logic
        if verdict in ("UNKNOWN", "SPECULATIVE"):
            self._consecutive_unknown += 1
            if self._consecutive_unknown >= self.CIRCUIT_UNKNOWN_THRESHOLD:
                if self._circuit_state != "open":
                    self._circuit_state = "open"
                    self._circuit_opened_at = time.time()
                    self._stats["circuit_opens"] += 1
                    logger.warning(
                        f"[DoSProtection] Circuit OPENED — {self._consecutive_unknown} consecutive UNKNOWN/SPECULATIVE"
                    )
        else:
            # Reset on PASS or FAIL
            if self._consecutive_unknown > 0:
                self._consecutive_unknown = 0
                if self._circuit_state == "half_open":
                    self._circuit_state = "closed"
                    logger.info("[DoSProtection] Circuit → closed (recovered)")

    def stats(self) -> dict[str, Any]:
        return {
            **self._stats,
            "circuit_state": self._circuit_state,
            "consecutive_unknown": self._consecutive_unknown,
            "current_concurrent": self._current_concurrent,
            "tracked_ips": len(self._requests_per_minute),
            "thresholds": {
                "max_per_minute": self.MAX_REQUESTS_PER_MINUTE,
                "max_per_hour": self.MAX_REQUESTS_PER_HOUR,
                "circuit_threshold": self.CIRCUIT_UNKNOWN_THRESHOLD,
                "max_concurrent": self.MAX_CONCURRENT,
            },
        }


__all__ = ["DoSAlert", "DoSProtectionEngine"]
