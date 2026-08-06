"""
SCP - Viet Nam | Self-Correcting Pipeline
Copyright (c) 2026 SCP Vietnam Project. All Rights Reserved.




License: See LICENSE file
Contact: scp-vietnam@example.com
"""
from __future__ import annotations

#!/usr/bin/env python3
"""
SCP V28 — Auto Backup DB.

Backup v13.db + hypothesis_zone.db mỗi 6 giờ (configurable).

Luồng:
    1. Mỗi N giờ, copy v13.db → backups/v13_YYYYMMDD_HHMMSS.db
    2. Giữ tối đa K backups gần nhất (auto-cleanup older)
    3. Verify backup bằng sqlite3 PRAGMA integrity_check
    4. Log vào backups/backup_log.jsonl

Usage:
    # Chạy 1 lần
    python -m scp.core.auto_backup --once

    # Chạy 24/7, mỗi 6 giờ
    python -m scp.core.auto_backup --loop --interval 6

    # Liệt kê backups
    python -m scp.core.auto_backup --list
"""

import argparse
import json
import os
import shutil
import signal
import sqlite3
import sys
import time
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(SCRIPT_DIR))  # add scp-runtime/ to path

import logging

from scp.core.db_manager import DATA_DIR

logger = logging.getLogger("scp.auto_backup")

# Backup directory
BACKUP_DIR = os.path.join(DATA_DIR, "backups")
os.makedirs(BACKUP_DIR, exist_ok=True)

# Defaults
DEFAULT_INTERVAL_HOURS = 6
DEFAULT_MAX_BACKUPS = 20  # giữ 20 backup gần nhất (≈ 5 ngày nếu mỗi 6h)


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _backup_log_path() -> str:
    return os.path.join(BACKUP_DIR, "backup_log.jsonl")


def backup_db(max_backups: int = DEFAULT_MAX_BACKUPS) -> dict:
    """
    Backup v13.db + hypothesis_zone.db (nếu tồn tại).
    Returns dict with status info.
    """
    ts = _timestamp()
    result = {
        "timestamp": datetime.now().astimezone().isoformat(),
        "ts": ts,
        "backups": [],
        "cleaned_up": [],
        "status": "ok",
        "error": None,
    }

    # Files to backup
    files_to_backup = [
        ("v13.db", f"v13_{ts}.db"),
        ("hypothesis_zone.db", f"hypothesis_zone_{ts}.db"),
    ]

    for src_name, dst_name in files_to_backup:
        src_path = os.path.join(DATA_DIR, src_name)
        dst_path = os.path.join(BACKUP_DIR, dst_name)

        if not os.path.exists(src_path):
            result["backups"].append({
                "source": src_name,
                "skipped": True,
                "reason": "file not found",
            })
            continue

        try:
            # Checkpoint WAL mode first (flush changes to main DB)
            try:
                conn = sqlite3.connect(src_path, timeout=30.0)
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                conn.close()
            except Exception as e:
                logger.warning(f"WAL checkpoint {src_name}: {e}")

            # Copy file
            shutil.copy2(src_path, dst_path)

            # Verify integrity
            integrity_ok = False
            try:
                conn = sqlite3.connect(dst_path, timeout=30.0)
                row = conn.execute("PRAGMA integrity_check").fetchone()
                conn.close()
                integrity_ok = row[0] == "ok" if row else False
            except Exception as e:
                logger.warning(f"Integrity check {dst_name}: {e}")

            size_kb = os.path.getsize(dst_path) / 1024
            result["backups"].append({
                "source": src_name,
                "destination": dst_name,
                "size_kb": round(size_kb, 1),
                "integrity_ok": integrity_ok,
                "skipped": False,
            })

            if not integrity_ok:
                result["status"] = "warning"
                result["error"] = f"Integrity check failed for {dst_name}"

        except Exception as e:
            logger.error(f"Backup {src_name} error: {e}")
            result["backups"].append({
                "source": src_name,
                "error": str(e),
                "skipped": False,
            })
            result["status"] = "error"
            result["error"] = str(e)

    # Cleanup old backups
    cleaned = cleanup_old_backups(max_backups)
    result["cleaned_up"] = cleaned

    # Append to log
    try:
        log_path = _backup_log_path()
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(result) + "\n")
    except Exception as e:
        logger.warning(f"Backup log write error: {e}")

    return result


def cleanup_old_backups(max_backups: int = DEFAULT_MAX_BACKUPS) -> list[str]:
    """Xóa backups cũ, giữ lại max_backups gần nhất (per file type)."""
    cleaned = []
    try:
        all_files = [f for f in os.listdir(BACKUP_DIR) if f.endswith(".db")]
        # Group by prefix (v13_ vs hypothesis_zone_)
        groups: dict[str, list[str]] = {}
        for f in all_files:
            # Extract prefix (everything before timestamp YYYYMMDD_HHMMSS)
            # v13_20260703_171729.db → "v13_"
            parts = f.rsplit("_", 2)  # ['v13', '20260703', '171729.db']
            if len(parts) == 3:
                prefix = parts[0] + "_"
                groups.setdefault(prefix, []).append(f)

        for _prefix, files in groups.items():
            # Sort by name desc (newest first due to timestamp)
            files.sort(reverse=True)
            # Delete files beyond max_backups
            for old_file in files[max_backups:]:
                old_path = os.path.join(BACKUP_DIR, old_file)
                try:
                    os.remove(old_path)
                    cleaned.append(old_file)
                except Exception as e:
                    logger.warning(f"Cleanup {old_file}: {e}")
    except Exception as e:
        logger.warning(f"Cleanup error: {e}")
    return cleaned


def list_backups() -> list[dict]:
    """Liệt kê tất cả backups."""
    backups = []
    try:
        for f in sorted(os.listdir(BACKUP_DIR), reverse=True):
            if not f.endswith(".db"):
                continue
            path = os.path.join(BACKUP_DIR, f)
            stat = os.stat(path)
            backups.append({
                "filename": f,
                "size_kb": round(stat.st_size / 1024, 1),
                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            })
    except Exception as e:
        logger.warning(f"List backups error: {e}")
    return backups


def main():
    parser = argparse.ArgumentParser(description="SCP V28 Auto Backup")
    parser.add_argument("--once", action="store_true", help="Backup 1 lần rồi exit")
    parser.add_argument("--loop", action="store_true", help="Chạy 24/7, mỗi --interval giờ")
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_HOURS,
                        help=f"Khoảng cách giữa 2 backup (giờ), default: {DEFAULT_INTERVAL_HOURS}")
    parser.add_argument("--max-backups", type=int, default=DEFAULT_MAX_BACKUPS,
                        help=f"Giữ tối đa N backups, default: {DEFAULT_MAX_BACKUPS}")
    parser.add_argument("--list", action="store_true", help="Liệt kê backups")
    parser.add_argument("--cleanup", action="store_true", help="Cleanup old backups only")
    args = parser.parse_args()

    if args.list:
        backups = list_backups()
        print(f"\n{'='*60}")
        print(f"  BACKUPS ({len(backups)} files in {BACKUP_DIR})")
        print(f"{'='*60}")
        for b in backups[:20]:
            print(f"  {b['filename']:40s} {b['size_kb']:>10.1f} KB  {b['modified']}")
        return

    if args.cleanup:
        cleaned = cleanup_old_backups(args.max_backups)
        print(f"Cleaned up {len(cleaned)} old backups")
        for f in cleaned:
            print(f"  - {f}")
        return

    if args.once:
        print(f"\n  Backup at {datetime.now().isoformat()}")
        result = backup_db(args.max_backups)
        print(f"  Status: {result['status']}")
        for b in result["backups"]:
            if b.get("skipped"):
                print(f"    [SKIP] {b['source']}: {b.get('reason', 'unknown')}")
            elif b.get("error"):
                print(f"    [ERR ] {b['source']}: {b['error']}")
            else:
                ok = "OK" if b.get("integrity_ok") else "WARN"
                print(f"    [{ok}] {b['source']} → {b['destination']} ({b['size_kb']} KB)")
        if result["cleaned_up"]:
            print(f"  Cleaned up {len(result['cleaned_up'])} old backups")
        return

    if args.loop:
        running = [True]
        def handler(sig, frame):
            running[0] = False
            print("\n  Shutting down gracefully...")
        signal.signal(signal.SIGINT, handler)

        print(f"\n  Auto-backup loop every {args.interval}h (max {args.max_backups} backups)")
        print(f"  Backup dir: {BACKUP_DIR}")
        print("  Press Ctrl+C to stop\n")

        cycle = 0
        while running[0]:
            cycle += 1
            print(f"  [{datetime.now().isoformat()}] Backup cycle #{cycle}")
            result = backup_db(args.max_backups)
            print(f"    Status: {result['status']}")
            for b in result["backups"]:
                if not b.get("skipped") and not b.get("error"):
                    ok = "OK" if b.get("integrity_ok") else "WARN"
                    print(f"    [{ok}] {b['source']} → {b['destination']} ({b['size_kb']} KB)")

            # Sleep
            sleep_seconds = args.interval * 3600
            # Sleep in 1-sec increments so Ctrl+C works
            slept = 0
            while running[0] and slept < sleep_seconds:
                time.sleep(1)
                slept += 1

        print(f"\n  Stopped after {cycle} cycles")


if __name__ == "__main__":
    main()
