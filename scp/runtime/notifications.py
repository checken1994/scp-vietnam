"""
SCP V102 — UserNotificationSystem
==================================
Báo cáo user real-time khi có sự kiện quan trọng.

Channels:
  1. Dashboard (real-time WebSocket push)
  2. Webhook (Slack/Discord/Telegram/configurable)
  3. Email (SMTP)
  4. File log (user_reports.jsonl — always)

User configures which channels via config.yaml.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
<<<<<<< HEAD
from typing import Any
=======
from typing import Any, Optional
>>>>>>> 847232d (Initial commit - SCP Vietnam V99)

# [ROOT-FIX Task 38-A / Issue 3] DNA #5 UNKNOWN > wrong answer: SEVERITY_ORDER
# was missing "high", "medium", "low" — these fell back to 0 (treated as
# "info") via SEVERITY_ORDER.get(severity, 0). Concrete impact: a "high"
# severity event from governance was compared against min_severity="warning"
# (which has order=1) and SKIPPED because 0 < 1. Now import from
# scp.meta.severity (single source of truth, Issue 1 fix) and include ALL
# severity levels. Backward-compatible fallback if severity.py unavailable.
try:
    from scp.meta.severity import Severity

    _SEVERITY_ORDER_RESOLVED = {
        Severity.INFO: 0,
        Severity.LOW: 1,
        Severity.MEDIUM: 2,
        Severity.WARNING: 3,
        Severity.HIGH: 4,
        Severity.CRITICAL: 5,
    }
except ImportError:  # pragma: no cover — fallback if severity.py unreachable
    _SEVERITY_ORDER_RESOLVED = {
        "info": 0, "low": 1, "medium": 2,
        "warning": 3, "high": 4, "critical": 5,
    }

logger = logging.getLogger("scp.runtime.notifications")


@dataclass
class NotificationConfig:
    """User notification configuration."""
    # Webhook (Slack/Discord/Telegram)
    webhook_url: str = ""
    webhook_enabled: bool = False

    # Email
    email_enabled: bool = False
    email_smtp_host: str = ""
    email_smtp_port: int = 587
    email_username: str = ""
    email_password: str = ""
    email_from: str = ""
    email_to: str = ""

    # Dashboard (always enabled if API server running)
    dashboard_enabled: bool = True

    # Severity threshold — only notify for >= this level
    min_severity: str = "warning"  # info|warning|critical

    # Rate limit — max notifications per hour
    max_per_hour: int = 50


class UserNotificationSystem:
    """Send real-time notifications to user via multiple channels.

    Naming convention: <Purpose>System (world standard).
    """

    # [ROOT-FIX Task 38-A / Issue 3] Was: {"info": 0, "warning": 1, "critical": 2}
    # Bug: "high"/"medium"/"low" fell back to 0 (treated as "info") →
    # high-severity events SKIPPED when min_severity="warning".
    # Fix: full ordered ladder 0..5, sourced from scp.meta.severity (Issue 1).
    # Severity is `str, Enum` so dict lookup with plain string "high" works
    # via hash-of-string (Severity.HIGH == "high" and hash matches).
    SEVERITY_ORDER = _SEVERITY_ORDER_RESOLVED

    def __init__(self, config: NotificationConfig = None, data_dir: str = "data"):
        self.config = config or NotificationConfig()
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._notifications_file = self.data_dir / "notifications.jsonl"
        self._recent: list[dict[str, Any]] = []
        self._hourly_count = 0
        self._hour_start = time.time()
        self._stats = {
            "total_sent": 0,
            "total_dashboard": 0,
            "total_webhook": 0,
            "total_email": 0,
            "total_rate_limited": 0,
        }

    def notify(
        self,
        event_type: str = "governance_event",
        severity: str = "warning",
        title: str = "",
        message: str = "",
<<<<<<< HEAD
        details: dict[str, Any] = None,
        actions_taken: list[str] = None,
=======
        details: Optional[dict[str, Any]] = None,
        actions_taken: Optional[list[str]] = None,
>>>>>>> 847232d (Initial commit - SCP Vietnam V99)
    ) -> dict[str, Any]:
        """Send notification to all configured channels.

        Args:
            event_type: attack_blocked|bypass_detected|false_positive|slow_query|daily_summary|governance_event|governance_kill|bypass_detected|canary_triggered
            severity: info|warning|critical
            title: Short title
            message: Detailed message
            details: Additional data
            actions_taken: What SCP did automatically

        Returns:
            Dict with delivery status per channel

        [P1-7 FIX] TẠI SAO: `event_type` and `severity` had no defaults, but all
        3 call sites in judge.py (KILL, bypass, canary) omit `event_type` and rely
        on kwargs for the rest → TypeError: missing 1 required positional argument
        → swallowed by `except Exception as e: pass` → Bug CV was cosmetically "fixed"
        (calls exist) but functionally DEAD. Giving defaults makes the existing
        calls work immediately; judge.py call sites will additionally be updated
        to pass explicit event_type values for proper routing/analytics.
        """
        # [P1-7] Backward-compat: older callers used positional (severity, title, message).
        # If the first positional arg looks like a severity level, treat it as such.
        if event_type in ("info", "warning", "critical") and severity not in ("info", "warning", "critical"):
            # caller did notify("warning", "title", "message") — shift args
            event_type, severity, title, message = "governance_event", event_type, severity, title or message, ""
        # Check severity threshold
        if self.SEVERITY_ORDER.get(severity, 0) < self.SEVERITY_ORDER.get(self.config.min_severity, 0):
            return {"skipped": True, "reason": "below_severity_threshold"}

        # Rate limit
        now = time.time()
        if now - self._hour_start > 3600:
            self._hour_start = now
            self._hourly_count = 0
        if self._hourly_count >= self.config.max_per_hour:
            self._stats["total_rate_limited"] += 1
            return {"skipped": True, "reason": "rate_limited"}
        self._hourly_count += 1

        notification = {
            "id": f"notif_{int(now*1000)}",
            "timestamp": now,
            "event_type": event_type,
            "severity": severity,
            "title": title,
            "message": message,
            "details": details or {},
            "actions_taken": actions_taken or [],
        }

        delivery = {"notification_id": notification["id"]}

        # 1. Always save to file
        self._save_to_file(notification)
        delivery["file"] = True

        # 2. Dashboard (real-time)
        if self.config.dashboard_enabled:
            self._recent.append(notification)
            self._stats["total_dashboard"] += 1
            delivery["dashboard"] = True

        # 3. Webhook (Slack/Discord/Telegram)
        if self.config.webhook_enabled and self.config.webhook_url:
            success = self._send_webhook(notification)
            delivery["webhook"] = success
            if success:
                self._stats["total_webhook"] += 1

        # 4. Email
        if self.config.email_enabled:
            success = self._send_email(notification)
            delivery["email"] = success
            if success:
                self._stats["total_email"] += 1

        self._stats["total_sent"] += 1
        return delivery

    def _save_to_file(self, notification: dict[str, Any]):
        """Save to JSONL file."""
        try:
            with open(self._notifications_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(notification, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.debug(f"[Notifications] File save error: {e}")

    def _send_webhook(self, notification: dict[str, Any]) -> bool:
        """Send to webhook (Slack/Discord/Telegram)."""
        try:
            import httpx
            # Format for Slack/Discord
            severity_emoji = {"info": "ℹ️", "warning": "⚠️", "critical": "🚨"}
            payload = {
                "text": f"{severity_emoji.get(notification['severity'], '📌')} {notification['title']}\n{notification['message']}",
                "username": "SCP V102",
            }
            # For Telegram
            if "telegram" in self.config.webhook_url:
                payload = {
                    "chat_id": "",
                    "text": f"{severity_emoji.get(notification['severity'], '📌')} *{notification['title']}*\n{notification['message']}",
                    "parse_mode": "Markdown",
                }

            import asyncio
            async def _send():
                async with httpx.AsyncClient(timeout=5) as client:
                    r = await client.post(self.config.webhook_url, json=payload)
                    return r.status_code in (200, 204)
            return asyncio.run(_send())
        except Exception as e:
            logger.debug(f"[Notifications] Webhook error: {e}")
            return False

    def _send_email(self, notification: dict[str, Any]) -> bool:
        """Send email notification."""
        try:
            import smtplib
            from email.mime.text import MIMEText

            msg = MIMEText(
                f"{notification['title']}\n\n{notification['message']}\n\n"
                f"Event: {notification['event_type']}\n"
                f"Severity: {notification['severity']}\n"
                f"Time: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(notification['timestamp']))}\n"
                f"Actions: {', '.join(notification.get('actions_taken', []))}"
            )
            msg["Subject"] = f"[SCP V102] {notification['title']}"
            msg["From"] = self.config.email_from
            msg["To"] = self.config.email_to

            with smtplib.SMTP(self.config.email_smtp_host, self.config.email_smtp_port) as server:
                server.starttls()
                server.login(self.config.email_username, self.config.email_password)
                server.send_message(msg)
            return True
        except Exception as e:
            logger.debug(f"[Notifications] Email error: {e}")
            return False

    def get_recent(self, limit: int = 20) -> list[dict[str, Any]]:
        """Get recent notifications for dashboard."""
        return self._recent[-limit:]

    def stats(self) -> dict[str, Any]:
        return {
            **self._stats,
            "hourly_count": self._hourly_count,
            "max_per_hour": self.config.max_per_hour,
            "webhook_enabled": self.config.webhook_enabled,
            "email_enabled": self.config.email_enabled,
            "dashboard_enabled": self.config.dashboard_enabled,
        }


__all__ = ["NotificationConfig", "UserNotificationSystem"]
