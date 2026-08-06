"""
SCP - Viet Nam | Self-Correcting Pipeline
Copyright (c) 2026 SCP Vietnam Project. All Rights Reserved.




License: See LICENSE file
Contact: scp-vietnam@example.com
"""
from __future__ import annotations

#!/usr/bin/env python3
"""
SCP V31C — Calibration Engine.

Vấn đề: SLM confidence hiện là con số tĩnh (0.85, 0.90, 0.95) — không phản ánh accuracy thực tế.

Giải pháp: Track confidence → accuracy mapping theo thời gian, auto-tune confidence.

Calibration metrics:
    - Brier score: 0 = perfect, 1 = worst
    - Log loss: lower = better
    - Calibration curve: bucket confidence [0-0.1, 0.1-0.2, ...] → actual accuracy
    - ECE (Expected Calibration Error): avg |confidence - accuracy| per bucket

Per-domain tracking:
    - math: calibration 1.0 (always right)
    - weather: calibration 0.85 (sometimes wrong)
    - crypto: calibration 0.92 (mostly right within tolerance)

Auto-tune strategy:
    - Nếu SLM confidence=0.95 nhưng actual accuracy=0.70 → reduce to 0.70
    - Apply per-domain calibration factor: confidence *= calibration_factor
    - Update factor mỗi N cycles (vd 100)
"""

import logging
import os
import sys
import time
from collections import defaultdict
from datetime import datetime
from typing import Any

logger = logging.getLogger("scp.calibration")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from scp.core.db_manager import db_exec, db_query_all, db_query_one, init_db


# ============================================================
# SCHEMA — Calibration table
# ============================================================
def init_calibration_db():
    """Tạo calibration tables."""
    # Calibration history — each verdict logged
    db_exec("""
        CREATE TABLE IF NOT EXISTS calibration_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            domain TEXT NOT NULL,
            slm_name TEXT,
            confidence REAL NOT NULL,
            actual_verdict TEXT NOT NULL,
            predicted_correct BOOLEAN,
            question TEXT,
            cycle_id INTEGER
        )
    """)
    db_exec("CREATE INDEX IF NOT EXISTS idx_calib_domain ON calibration_history(domain)")
    db_exec("CREATE INDEX IF NOT EXISTS idx_calib_timestamp ON calibration_history(timestamp)")

    # Calibration factors — per-domain tuned confidence multipliers
    db_exec("""
        CREATE TABLE IF NOT EXISTS calibration_factors (
            domain TEXT PRIMARY KEY,
            factor REAL NOT NULL DEFAULT 1.0,
            sample_count INTEGER DEFAULT 0,
            last_updated TEXT,
            brier_score REAL,
            ece REAL,
            accuracy REAL
        )
    """)


# ============================================================
# CALIBRATION ENGINE
# ============================================================
class CalibrationEngine:
    """
    Calibration Engine — track confidence accuracy, auto-tune factors.

    Usage:
        engine = CalibrationEngine()

        # Log verdict
        engine.log_verdict(domain="math", slm_name="MathSLM",
                          confidence=0.95, actual_verdict="PASS",
                          question="Tính 2+3")

        # Get calibration factor for a domain
        factor = engine.get_factor("math")  # e.g., 0.98

        # Apply: tuned_confidence = confidence * factor
        tuned = 0.95 * factor

        # Recompute factors (run periodically)
        engine.recompute_factors()
    """

    # Recompute after N new samples
    RECOMPUTE_THRESHOLD = 50

    # Domains to track
    TRACKED_DOMAINS = [
        # Original domains
        "math", "logic", "statistics", "reality", "biology",
        "geography", "history", "chemistry", "weather",
        "finance", "conversion", "unknown",
        # [V79] Added V46 domains
        "medical", "technology", "sports", "legal", "arts",
        # [V79] Added V73+ domains
        "general", "entertainment", "religion", "food", "city",
        "astronomy",
        # [V79] Added V78 domains
        "holiday", "animal_facts", "advice", "chuck_norris",
    ]

    def __init__(self):
        # [V104.23 #2 FIX] Instance buffer + lock (was: class attr → race)
        self._buffer = []
        self._buffer_lock = __import__('threading').Lock()
        # [V104.32 #17] Instance-level _log_buffer (was: class attr → race)
        self._log_buffer = []
        init_db()
        init_calibration_db()
        self._factor_cache: dict[str, tuple[float, float]] = {}  # domain → (factor, ts)
        self._CACHE_TTL = 60  # 1 min
        # [V104.40 #E] TẠI SAO: _factor_cache read by get_factor (hot path, multiple threads)
        # while recompute_factors writes/invalidates → race (stale/half-updated factor).
        # Fix: separate lock for factor cache.
        self._factor_lock = __import__('threading').Lock()
        # [V41.1] Reset all factors to 1.0 on init — break PARTIAL death spiral
        self._reset_factors_if_needed()

    def _reset_factors_if_needed(self):
        """[V41.1] Reset calibration factors to 1.0 if they're too low (< 0.7)."""
        try:
            # [V41.2] Check if any factor < 0.7 (death spiral threshold)
            rows = db_query_all("SELECT domain, factor FROM calibration_factors WHERE factor < 0.7")
            if rows:
                for r in rows:
                    db_exec(
                        "UPDATE calibration_factors SET factor = 1.0 WHERE domain = ?",
                        (r["domain"],)
                    )
                    logger.info(f"Reset calibration factor for {r['domain']}: {r['factor']:.3f} → 1.0")
        except Exception as e:
            logger.debug(f"[V104.37] meta/calibration_engine.py: e={e}")

    # ============================================================
    # LOG VERDICT (V32: batch mode)
    # ============================================================
    # In-memory buffer for batch INSERT
    # [V104.32 #17] _log_buffer moved to __init__ (was: class attr → race)
    _BUFFER_SIZE = 20  # Flush to DB every 20 verdicts

    def log_verdict(self, domain: str, slm_name: str,
                    confidence: float, actual_verdict: str,
                    question: str = "", cycle_id: int = 0) -> None:
        """Log 1 verdict vào calibration history.

        [V32] Buffer in-memory, batch INSERT mỗi 20 verdicts.
        [V39] Skip for deterministic domains — saves 60% DB writes.
        [V41.1] Skip PARTIAL/UNKNOWN/CONFLICT — only log PASS/FAIL.
                PARTIAL = "chưa đủ điều kiện", không phải "sai" → không nên giảm confidence.
        """
        # [V39] Skip logging for deterministic — they're always right, no calibration needed
        if domain in ("math", "logic", "statistics", "reality"):
            return

        # [V41.1] ONLY log PASS and FAIL — PARTIAL/UNKNOWN cause death spiral
        if actual_verdict not in ("PASS", "FAIL"):
            return

        # [V64 FIX] predicted_correct logic was WRONG:
        # Trước V64: if confidence > 0.5 → predicted_correct = (verdict == "PASS")
        #   → Khi verdict=FAIL + confidence=0.85 → predicted_correct=False → accuracy=100%
        #   → Brier=(0.85-0)²=0.72 → Brier cao nhưng accuracy=100% (vô lý!)
        #
        # V64: predicted_correct = True means "SLM correctly predicted the verdict"
        #   - SLM confident (conf>0.5) + verdict=PASS → predicted PASS, actual PASS → correct
        #   - SLM confident (conf>0.5) + verdict=FAIL → predicted PASS, actual FAIL → WRONG
        #   - SLM not confident (conf<=0.5) + verdict=FAIL → predicted FAIL, actual FAIL → correct
        #   - SLM not confident (conf<=0.5) + verdict=PASS → predicted FAIL, actual PASS → WRONG
        #
        # [V82 FIX] Calibration logic was BROKEN — accuracy always = 1.0
        #
        # V79-V81 bug: "FAIL + conf > 0.7 → predicted_correct = True"
        #   → SLM always returns conf > 0.7 → predicted_correct always True
        #   → accuracy = 1.0 → factor = 1.0 → SCP NEVER self-corrects
        #
        # V82 fix: predicted_correct measures "was SCP's PREDICTION correct?"
        #   - SCP predicts "AI is right" when confidence > 0.5 (would PASS)
        #   - SCP predicts "AI is wrong" when confidence > 0.5 (would FAIL)
        #   - actual_outcome: PASS = AI was right, FAIL = AI was wrong
        #
        # For calibration:
        #   - PASS + conf > 0.5: SCP predicted "right", actual "right" → correct
        #   - FAIL + conf > 0.5: SCP predicted "wrong", actual "wrong" → correct
        #     BUT: we don't know if AI was actually wrong (no ground truth)
        #
        # Practical approach: measure CONFIDENCE RELIABILITY
        #   - High confidence (conf > 0.7) + PASS → reliable → correct
        #   - High confidence (conf > 0.7) + FAIL → MAY be false FAIL → NOT always correct
        #   - Low confidence (conf <= 0.5) + any → uncertain → not counted
        #
        # [V82] New logic: FAIL is NOT automatically correct
        #   - PASS + conf > 0.5 → correct (SCP confirmed AI)
        #   - FAIL + conf > 0.7 → correct ONLY if we can verify AI was wrong
        #     Since we can't verify, treat as 50/50 → don't count (None)
        #   - FAIL + conf 0.5-0.7 → uncertain FAIL → incorrect (false positive)
        #   - PASS + conf <= 0.5 → uncertain PASS → incorrect (should be more confident)
        # [V104.39 #C] TẠI SAO: old V82 logic counted FAIL+conf>0.7 as "correct"
        # → accuracy inflated → factor→1.0 → no calibration. FAIL means AI was WRONG
        # (regardless of confidence). Only PASS = correct.
        # UNKNOWN/PARTIAL = neutral (don't count toward accuracy).
        if actual_verdict == "PASS":
            predicted_correct = True  # PASS = AI correct
        elif actual_verdict == "FAIL":
            predicted_correct = False  # FAIL = AI wrong (confidence irrelevant)
        else:  # UNKNOWN, PARTIAL, CONFLICT, SKIP
            predicted_correct = None  # neutral — don't count

        ts = datetime.now().astimezone().isoformat()
        # Add to buffer
        # [EXEC-3] TẠI SAO: _log_buffer was race-prone under 16 workers —
        # _buffer_lock declared but never used. Wrap append + len check in
        # _buffer_lock; decide flush inside lock, call flush OUTSIDE lock
        # (flush re-acquires _buffer_lock to snapshot+clear) → no deadlock.
        needs_flush = False
        with self._buffer_lock:
            self._log_buffer.append((ts, domain, slm_name, confidence, actual_verdict,
                                      predicted_correct, question[:200], cycle_id))
            # Flush if buffer full
            if len(self._log_buffer) >= self._BUFFER_SIZE:
                needs_flush = True
        if needs_flush:
            self._flush_log_buffer()

    def _flush_log_buffer(self) -> None:
        """Flush buffered logs to DB (batch INSERT)."""
        # [EXEC-3] Snapshot+clear under _buffer_lock (atomic), then do DB I/O
        # outside the lock so other workers can keep appending during the slow
        # INSERT batch. Snapshot is a copy → safe to iterate without lock.
        with self._buffer_lock:
            if not self._log_buffer:
                return
            to_flush = list(self._log_buffer)
            self._log_buffer.clear()
        try:
            # [V37 FIX] db_exec auto-commits each call — don't use BEGIN/COMMIT
            # Just do individual INSERTs (auto-commit handles persistence)
            for entry in to_flush:
                db_exec("""
                    INSERT INTO calibration_history
                    (timestamp, domain, slm_name, confidence, actual_verdict,
                     predicted_correct, question, cycle_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, entry)

            # [V79 FIX] Recompute factors after flush — was missing!
            # Before: calibration_history had 40+ rows but calibration_factors = 0
            # Now: recompute after each flush (min_samples=5 for fast feedback)
            try:
                self.recompute_factors(min_samples=5)
            except Exception as e:
                logger.debug(f"Recompute factors error: {e}")
        except Exception as e:
            logger.warning(f"Calibration batch flush error: {e}")
            # Note: buffer already cleared under lock above — lost batch on DB
            # error is acceptable (next cycle re-logs). Do NOT re-add to buffer.

    def flush(self) -> None:
        """Force flush buffer (call khi shutdown)."""
        self._flush_log_buffer()

    # ============================================================
    # GET FACTOR
    # ============================================================
    def get_factor(self, domain: str) -> float:
        """
        Get calibration factor for domain.
        Returns multiplier (0-1) to apply to confidence.

        VD: factor=0.85 means SLM over-confident → multiply confidence by 0.85

        [V104.40 #E] TẠI SAO: was no lock → race with recompute_factors
        (which invalidates cache mid-read). Fix: _factor_lock protects cache access.
        """
        now = time.time()
        # [V104.40 #E] lock-protected cache read
        with self._factor_lock:
            if domain in self._factor_cache:
                factor, ts = self._factor_cache[domain]
                if now - ts < self._CACHE_TTL:
                    return factor

        try:
            row = db_query_one(
                "SELECT factor FROM calibration_factors WHERE domain = ?",
                (domain,)
            )
            if row:
                factor = row["factor"]
            else:
                factor = 1.0  # default — no calibration
        except Exception:
            factor = 1.0

        # [V104.40 #E] lock-protected cache write
        with self._factor_lock:
            self._factor_cache[domain] = (factor, now)
        return factor

    def apply_calibration(self, confidence: float, domain: str) -> float:
        """Apply calibration factor to confidence."""
        factor = self.get_factor(domain)
        tuned = confidence * factor
        # Clamp to [0, 1]
        return max(0.0, min(1.0, tuned))

    # ============================================================
    # RECOMPUTE FACTORS
    # ============================================================
    def recompute_factors(self, min_samples: int = 10) -> dict[str, Any]:
        """
        Recompute calibration factors from history.

        [V41.1] Only count PASS/FAIL — exclude PARTIAL/UNKNOWN/CONFLICT.
                 PARTIAL = "chưa đủ điều kiện" ≠ "sai" → không giảm confidence.
        """
        results: dict[str, Any] = {}

        for domain in self.TRACKED_DOMAINS:
            try:
                # [V41.1] Only get PASS/FAIL verdicts (not PARTIAL/UNKNOWN)
                rows = db_query_all(
                    "SELECT confidence, actual_verdict, predicted_correct "
                    "FROM calibration_history WHERE domain = ? "
                    "AND actual_verdict IN ('PASS', 'FAIL') "
                    "ORDER BY timestamp DESC LIMIT 1000",
                    (domain,)
                )
                if not rows or len(rows) < min_samples:
                    results[domain] = {"skipped": "insufficient samples"}
                    continue

                rows = [dict(r) for r in rows]
                sample_count = len(rows)

                # Calculate accuracy
                # Only count verdicts where confidence > 0.5 (made a prediction)
                predictions = [r for r in rows if r["predicted_correct"] is not None]
                if not predictions:
                    results[domain] = {"skipped": "no predictions"}
                    continue

                correct_count = sum(1 for p in predictions if p["predicted_correct"])
                accuracy = correct_count / len(predictions)

                # [V47 FIX] Compute fail_rate CORRECTLY (was undefined → NameError → silent discard)
                # fail_rate = proportion of FAIL verdicts among predictions
                fail_count = sum(1 for p in predictions if p["actual_verdict"] == "FAIL")
                fail_rate = fail_count / len(predictions)

                # [V47 FIX] Differentiate 2 types of FAIL:
                #   - SLM-caught-AI-error: SLM was HIGH confidence (>0.7) about real value
                #     and AI was wrong → SLM did its job WELL → should NOT reduce confidence
                #   - SLM-uncertain: SLM was LOW confidence and verdict was FAIL
                #     → SLM correctly uncertain → no penalty
                # Only penalize when SLM was HIGH confidence but reality_check FAILED
                # (SLM gave wrong real value) — but this is rare since SLM uses verified sources.
                slm_caught_errors = sum(
                    1 for p in predictions
                    if p["actual_verdict"] == "FAIL" and p["confidence"] > 0.7
                )
                slm_caught_rate = slm_caught_errors / max(1, fail_count)

                # [V101 FIX] Brier score — V82 logic was WRONG
                # V82: actual = 1 for PASS, 0 for FAIL → penalized correct FAILs
                #   When SCP correctly FAILs with conf=0.85: Brier=(0.85-0)²=0.72 (BAD score for GOOD behavior)
                #
                # V101: Use predicted_correct as "actual" — measures confidence RELIABILITY
                #   High conf + correct = low Brier (good — confident and right)
                #   High conf + wrong = high Brier (bad — overconfident)
                #   Low conf + correct = moderate Brier (underconfident)
                #   Low conf + wrong = low Brier (good — appropriately uncertain)
                brier_sum = 0.0
                brier_count = 0
                for p in predictions:
                    pc = p.get("predicted_correct")
                    if pc is None:
                        continue  # skip uncertain predictions
                    actual = 1.0 if pc else 0.0
                    brier_sum += (p["confidence"] - actual) ** 2
                    brier_count += 1
                brier = brier_sum / brier_count if brier_count > 0 else 0.0

                # [V101 FIX] ECE — same fix as Brier
                buckets: dict[int, list[tuple[float, int]]] = defaultdict(list)
                for p in predictions:
                    pc = p.get("predicted_correct")
                    if pc is None:
                        continue
                    actual = 1 if pc else 0
                    bucket = min(9, int(p["confidence"] * 10))
                    buckets[bucket].append((p["confidence"], actual))

                # Calculate avg_confidence
                avg_confidence = sum(p["confidence"] for p in predictions) / len(predictions) if predictions else 0.0

                ece = 0.0
                for _bucket_idx, items in buckets.items():
                    if not items:
                        continue
                    bucket_conf = sum(c for c, _ in items) / len(items)
                    bucket_acc = sum(a for _, a in items) / len(items)
                    weight = len(items) / len(predictions)
                    ece += weight * abs(bucket_conf - bucket_acc)

                # [V90 FIX] Compute factor based on ACCURACY, not pass_rate
                # A FAIL verdict where SLM correctly catches a wrong answer = CORRECT
                # Old formula used pass_rate which penalized correct FAILs — wrong!
                # New: if accuracy >= 0.8, factor = 1.0 (domain is reliable)
                #      if accuracy < 0.8, factor = accuracy (proportional reduction)
                if accuracy >= 0.8:
                    factor = 1.0  # Domain is reliable — don't penalize
                else:
                    factor = max(0.5, accuracy)  # Proportional to accuracy

                # [V82] Removed V47 "preserve verified knowledge" override
                # Was: if fail_rate >= 0.40 and slm_caught_rate >= 0.70 → factor = 1.0
                # This always triggered (SLM always conf > 0.7) → factor always 1.0
                # Now: factor reflects actual pass_rate vs confidence

                # Update DB
                ts = datetime.now().astimezone().isoformat()
                db_exec("""
                    INSERT OR REPLACE INTO calibration_factors
                    (domain, factor, sample_count, last_updated, brier_score, ece, accuracy)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (domain, factor, sample_count, ts, brier, ece, accuracy))

                # Invalidate cache — [V104.40 #E] lock-protected
                with self._factor_lock:
                    if domain in self._factor_cache:
                        del self._factor_cache[domain]

                results[domain] = {
                    "factor": round(factor, 3),
                    "sample_count": sample_count,
                    "brier_score": round(brier, 4),
                    "ece": round(ece, 4),
                    "accuracy": round(accuracy, 3),
                    "avg_confidence": round(avg_confidence, 3),
                    "fail_rate": round(fail_rate, 3),
                    "slm_caught_rate": round(slm_caught_rate, 3),
                    "verified_knowledge_preserved": (fail_rate >= 0.40 and slm_caught_rate >= 0.70),
                }
            except Exception as e:
                results[domain] = {"error": str(e)}

        return results

    # ============================================================
    # STATS / REPORT
    # ============================================================
    def get_stats(self) -> dict[str, Any]:
        """Get calibration stats cho tất cả domains."""
        try:
            total = db_query_one("SELECT COUNT(*) as cnt FROM calibration_history")["cnt"]
            by_domain_rows = db_query_all(
                "SELECT domain, COUNT(*) as cnt, AVG(confidence) as avg_conf "
                "FROM calibration_history GROUP BY domain ORDER BY cnt DESC"
            )
            by_domain = {r["domain"]: {"count": r["cnt"], "avg_conf": round(r["avg_conf"] or 0, 3)}
                         for r in by_domain_rows} if by_domain_rows else {}

            factors_rows = db_query_all(
                "SELECT domain, factor, sample_count, brier_score, ece, accuracy, last_updated "
                "FROM calibration_factors ORDER BY domain"
            )
            factors = {r["domain"]: dict(r) for r in factors_rows} if factors_rows else {}

            return {
                "total_verdicts": total,
                "by_domain": by_domain,
                "calibration_factors": factors,
            }
        except Exception as e:
            return {"error": str(e)}

    def get_calibration_curve(self, domain: str, buckets: int = 10) -> list[dict]:
        """
        Get calibration curve cho 1 domain.
        Returns: [{bucket, count, avg_confidence, accuracy}]
        """
        try:
            rows = db_query_all(
                "SELECT confidence, actual_verdict FROM calibration_history "
                "WHERE domain = ? AND predicted_correct IS NOT NULL "
                "ORDER BY timestamp DESC LIMIT 1000",
                (domain,)
            )
            if not rows:
                return []

            bucket_data: dict[int, list[tuple[float, int]]] = defaultdict(list)
            for r in rows:
                actual = 1 if r["actual_verdict"] == "PASS" else 0
                bucket_idx = min(buckets - 1, int(r["confidence"] * buckets))
                bucket_data[bucket_idx].append((r["confidence"], actual))

            curve = []
            for i in range(buckets):
                items = bucket_data.get(i, [])
                if items:
                    avg_conf = sum(c for c, _ in items) / len(items)
                    acc = sum(a for _, a in items) / len(items)
                else:
                    avg_conf = 0
                    acc = 0
                curve.append({
                    "bucket": f"{i/buckets:.1f}-{(i+1)/buckets:.1f}",
                    "count": len(items),
                    "avg_confidence": round(avg_conf, 3),
                    "accuracy": round(acc, 3),
                    "gap": round(abs(avg_conf - acc), 3),
                })
            return curve
        except Exception as e:
            logger.warning(f"Calibration curve error: {e}")
            return []


# ============================================================
# MAIN
# ============================================================
def main():
    import argparse
    parser = argparse.ArgumentParser(description="SCP V31C Calibration Engine")
    parser.add_argument("--stats", action="store_true", help="Show calibration stats")
    parser.add_argument("--recompute", action="store_true", help="Recompute all factors")
    parser.add_argument("--curve", type=str, help="Show calibration curve for domain")
    args = parser.parse_args()

    engine = CalibrationEngine()

    if args.stats:
        stats = engine.get_stats()
        print(f"\n{'='*70}")
        print("  CALIBRATION STATS")
        print(f"{'='*70}")
        print(f"  Total verdicts logged: {stats.get('total_verdicts', 0)}")
        print("\n  By domain:")
        for d, info in stats.get("by_domain", {}).items():
            print(f"    {d:15s} count={info['count']:>5} avg_conf={info['avg_conf']}")
        print("\n  Calibration factors:")
        for d, info in stats.get("calibration_factors", {}).items():
            print(f"    {d:15s} factor={info['factor']:.3f} samples={info['sample_count']:>5} "
                  f"acc={info.get('accuracy', 0):.2%} brier={info.get('brier_score', 0):.3f} "
                  f"ece={info.get('ece', 0):.3f}")
        return

    if args.recompute:
        print("\n  Recomputing calibration factors...")
        results = engine.recompute_factors(min_samples=5)  # Lower threshold for demo
        print("\n  Results:")
        for d, info in results.items():
            if "error" in info or "skipped" in info:
                print(f"    {d:15s} {info}")
            else:
                print(f"    {d:15s} factor={info['factor']:.3f} samples={info['sample_count']:>5} "
                      f"acc={info['accuracy']:.2%} brier={info['brier_score']:.3f}")
        return

    if args.curve:
        curve = engine.get_calibration_curve(args.curve)
        print(f"\n  Calibration curve for '{args.curve}':")
        print(f"  {'Bucket':<12} {'Count':>6} {'Conf':>6} {'Acc':>6} {'Gap':>6}")
        for c in curve:
            print(f"  {c['bucket']:<12} {c['count']:>6} {c['avg_confidence']:>6.2f} "
                  f"{c['accuracy']:>6.2f} {c['gap']:>6.2f}")
        return

    print("Use --stats, --recompute, or --curve <domain>")


if __name__ == "__main__":
    main()
