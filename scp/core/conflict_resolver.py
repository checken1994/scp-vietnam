"""
SCP - Viet Nam | Self-Correcting Pipeline
Copyright (c) 2026 SCP Vietnam Project. All Rights Reserved.




License: See LICENSE file
Contact: scp-vietnam@example.com
"""
from __future__ import annotations

#!/usr/bin/env python3
"""
SCP V28 — Knowledge Conflict Resolution.

Khi nhiều source cho giá trị khác nhau cho cùng entity+attribute:
    VD: CoinGecko: BTC = $97000  vs  Binance: BTC = $97200

Module này resolve conflicts bằng các chiến lược:
    1. MAJORITY VOTE       — pick value được nhiều source confirm nhất
    2. WEIGHTED AVERAGE    — avg có weight = source_confidence
    3. LATEST_WINS         — pick value mới nhất (timestamp)
    4. MEDIAN              — pick median value (outlier-resistant)
    5. CONFLICT_LOG        — log conflict, keep all values, flag for review

Mỗi source có confidence weight:
    CODATA:        1.00 (physical constants — never change)
    PubChem:       0.95 (authoritative)
    Frankfurter:   0.90 (central bank data)
    CoinGecko:     0.85 (aggregated)
    Open-Meteo:    0.85 (real-time)
    REST Countries:0.85
    Wikipedia:     0.70 (community-edited)
    LocalDB:       0.60 (cache, may be stale)
    AI_generated:  0.30 (unverified)

Usage:
    from scp.core.conflict_resolver import ConflictResolver, resolve_value

    values = [
        {"value": 97000, "source": "CoinGecko", "ts": "2026-07-03T10:00:00"},
        {"value": 97200, "source": "Binance",   "ts": "2026-07-03T10:01:00"},
        {"value": 97050, "source": "Kraken",    "ts": "2026-07-03T10:00:30"},
    ]
    result = resolve_value(values, strategy="weighted_avg")
    # → {"value": 97083.33, "source": "weighted_avg(CoinGecko,Binance,Kraken)", "confidence": 0.85}
"""

import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("scp.conflict_resolver")


# Source confidence weights
SOURCE_WEIGHTS: dict[str, float] = {
    "CODATA": 1.00,
    "PubChem": 0.95,
    "PubChem(cached)": 0.95,
    "Frankfurter": 0.90,
    "REST Countries": 0.85,
    "REST Countries API": 0.85,
    "Open-Meteo": 0.85,
    "CoinGecko": 0.85,
    "Wikidata": 0.75,
    "Wikipedia": 0.70,
    "LocalDB": 0.60,
    "Local Geography Database": 0.60,
    "Local History Database": 0.60,
    "KnowledgeCache": 0.55,
    "AI_generated": 0.30,
    "PythonAST": 0.99,        # deterministic — highest trust
    "PythonMath": 0.99,
}


@dataclass
class ConflictResult:
    """Result của conflict resolution."""
    value: Any
    source: str
    confidence: float
    strategy: str
    conflict_detected: bool
    conflict_count: int
    all_values: list[dict] = field(default_factory=list)
    reason: str = ""


def get_source_weight(source: str | None) -> float:
    """
    Get confidence weight cho source.

    [V48 FIX] Hỗ trợ các biến thể source name:
      - "weighted(PubChem)" → PubChem weight (0.95)
      - "weighted(CoinGecko, Binance)" → max weight
      - "PubChem(cached)" → PubChem weight
    """
    if not source:
        return 0.5
    # Direct match
    if source in SOURCE_WEIGHTS:
        return SOURCE_WEIGHTS[source]
    # [V48] Partial match — find longest matching source name
    best_weight = 0.5
    best_len = 0
    for src_name, weight in SOURCE_WEIGHTS.items():
        if src_name in source and len(src_name) > best_len:
            best_weight = weight
            best_len = len(src_name)
    return best_weight


def _to_float(v: Any) -> float | None:
    """Convert value to float, None nếu không được."""
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _detect_conflict(values: list[dict]) -> bool:
    """Check nếu có conflict (>=2 distinct values)."""
    if len(values) < 2:
        return False
    distinct = set()
    for v in values:
        val = v.get("value")
        if val is not None:
            # [V104.17 #7 FIX] Normalize numbers before comparing (was: str(val))
            try:
                normalized = str(float(val))
            except (ValueError, TypeError):
                normalized = str(val)
            distinct.add(normalized)
    return len(distinct) > 1


# ============================================================
# STRATEGIES
# ============================================================
def resolve_majority_vote(values: list[dict]) -> ConflictResult:
    """Pick value được nhiều source confirm nhất."""
    if not values:
        return ConflictResult(None, "none", 0.0, "majority_vote",
                              False, 0, [], "No values")

    # Group by value (as string for stability)
    by_value: dict[str, list[dict]] = defaultdict(list)
    for v in values:
        val = v.get("value")
        if val is not None:
            try:
                normalized = str(float(val))
            except (ValueError, TypeError):
                normalized = str(val)
            by_value[normalized].append(v)

    if not by_value:
        return ConflictResult(None, "none", 0.0, "majority_vote",
                              False, 0, values, "No valid values")

    # Find value with most votes (weighted by source confidence)
    best_val_str = ""
    best_weight = -1
    for val_str, group in by_value.items():
        weight = sum(get_source_weight(g.get("source")) for g in group)
        if weight > best_weight:
            best_weight = weight
            best_val_str = val_str

    # Get original value (parse back)
    best_group = by_value[best_val_str]
    original_value = best_group[0].get("value")
    sources = [g.get("source", "?") for g in best_group]

    conflict = _detect_conflict(values)
    confidence = min(1.0, best_weight / max(1, sum(get_source_weight(v.get("source")) for v in values)))

    return ConflictResult(
        value=original_value,
        source=f"majority_vote({','.join(sources[:3])})",
        confidence=confidence,
        strategy="majority_vote",
        conflict_detected=conflict,
        conflict_count=len(by_value),
        all_values=values,
        reason=f"Picked value with {len(best_group)}/{len(values)} votes (weight={best_weight:.2f})",
    )


def resolve_weighted_avg(values: list[dict]) -> ConflictResult:
    """Weighted average — chỉ cho numeric values."""
    nums: list[tuple[float, float, str]] = []
    for v in values:
        f = _to_float(v.get("value"))
        if f is not None:
            w = get_source_weight(v.get("source"))
            nums.append((f, w, v.get("source", "?")))

    if not nums:
        return ConflictResult(None, "none", 0.0, "weighted_avg",
                              False, 0, values, "No numeric values")

    total_w = sum(w for _, w, _ in nums)
    if total_w == 0:
        avg = sum(f for f, _, _ in nums) / len(nums)
    else:
        avg = sum(f * w for f, w, _ in nums) / total_w

    conflict = _detect_conflict(values)
    sources = [s for _, _, s in nums]
    confidence = min(1.0, total_w / max(1, len(nums)))

    return ConflictResult(
        value=avg,
        source=f"weighted_avg({','.join(sources[:3])})",
        confidence=confidence,
        strategy="weighted_avg",
        conflict_detected=conflict,
        conflict_count=len(set(f for f, _, _ in nums)),
        all_values=values,
        reason=f"Avg of {len(nums)} values, total_weight={total_w:.2f}",
    )


def resolve_latest_wins(values: list[dict]) -> ConflictResult:
    """Pick value mới nhất (by timestamp)."""
    if not values:
        return ConflictResult(None, "none", 0.0, "latest_wins",
                              False, 0, [], "No values")

    # Sort by timestamp desc
    def _ts(v):
        ts = v.get("ts") or v.get("timestamp") or ""
        return str(ts)
    sorted_vals = sorted(values, key=_ts, reverse=True)

    best = sorted_vals[0]
    conflict = _detect_conflict(values)

    return ConflictResult(
        value=best.get("value"),
        source=best.get("source", "?"),
        confidence=get_source_weight(best.get("source")),
        strategy="latest_wins",
        conflict_detected=conflict,
        conflict_count=len(set(str(v.get("value")) for v in values if v.get("value") is not None)),
        all_values=values,
        reason=f"Latest from {best.get('source')} at {best.get('ts') or best.get('timestamp')}",
    )


def resolve_median(values: list[dict]) -> ConflictResult:
    """Pick median (outlier-resistant) — chỉ cho numeric."""
    nums: list[tuple[float, str]] = []
    for v in values:
        f = _to_float(v.get("value"))
        if f is not None:
            nums.append((f, v.get("source", "?")))

    if not nums:
        return ConflictResult(None, "none", 0.0, "median",
                              False, 0, values, "No numeric values")

    sorted_nums = sorted(nums, key=lambda x: x[0])
    n = len(sorted_nums)
    if n % 2 == 1:
        median_val = sorted_nums[n // 2][0]
        median_src = sorted_nums[n // 2][1]
    else:
        median_val = (sorted_nums[n // 2 - 1][0] + sorted_nums[n // 2][0]) / 2
        median_src = f"{sorted_nums[n // 2 - 1][1]}+{sorted_nums[n // 2][1]}"

    conflict = _detect_conflict(values)

    return ConflictResult(
        value=median_val,
        source=f"median({median_src})",
        confidence=0.75,
        strategy="median",
        conflict_detected=conflict,
        conflict_count=len(set(v for v, _ in nums)),
        all_values=values,
        reason=f"Median of {n} values",
    )


# ============================================================
# MAIN RESOLVER
# ============================================================
def resolve_value(values: list[dict],
                  strategy: str = "weighted_avg") -> ConflictResult:
    """
    Resolve conflicts giữa nhiều giá trị.

    Args:
        values: List of {"value": Any, "source": str, "ts": str (optional)}
        strategy: "majority_vote" / "weighted_avg" / "latest_wins" / "median"

    Returns:
        ConflictResult
    """
    if not values:
        return ConflictResult(None, "none", 0.0, strategy,
                              False, 0, [], "No values provided")

    if len(values) == 1:
        v = values[0]
        return ConflictResult(
            value=v.get("value"),
            source=v.get("source", "?"),
            confidence=get_source_weight(v.get("source")),
            strategy=strategy,
            conflict_detected=False,
            conflict_count=1,
            all_values=values,
            reason="Single value — no conflict",
        )

    strategies = {
        "majority_vote": resolve_majority_vote,
        "weighted_avg": resolve_weighted_avg,
        "latest_wins": resolve_latest_wins,
        "median": resolve_median,
    }
    handler = strategies.get(strategy)
    if not handler:
        return ConflictResult(None, "none", 0.0, strategy,
                              False, 0, values, f"Unknown strategy: {strategy}")

    return handler(values)


# ============================================================
# CONFLICT LOGGER — Log conflicts to DB for later review
# ============================================================
def log_conflict(entity: str, attribute: str, values: list[dict],
                 resolved: ConflictResult) -> None:
    """Log conflict vào knowledge_summaries (anomaly_count field)."""
    try:
        from scp.core.db_manager import db_exec
        # Update conflict_count in knowledge_summaries
        db_exec("""
            UPDATE knowledge_summaries
            SET conflict_count = conflict_count + 1,
                value_distribution = ?
            WHERE entity = ? AND attribute = ?
        """, (
            json.dumps({"values": [str(v.get("value")) for v in values],
                        "resolved": str(resolved.value),
                        "strategy": resolved.strategy}),
            entity.lower(), attribute
        ))
    except Exception as e:
        logger.warning(f"Conflict log error: {e}")


# ============================================================
# MAIN
# ============================================================
def main():
    import argparse
    parser = argparse.ArgumentParser(description="SCP V28 Conflict Resolver")
    parser.add_argument("--test", action="store_true", help="Run test cases")
    parser.add_argument("--strategy", choices=["majority_vote", "weighted_avg", "latest_wins", "median"],
                        default="weighted_avg")
    args = parser.parse_args()

    if args.test:
        print(f"\n{'='*60}")
        print("  CONFLICT RESOLVER TESTS")
        print(f"{'='*60}")

        test_cases = [
            # No conflict — single value
            {
                "name": "Single value",
                "values": [{"value": 100, "source": "CODATA", "ts": "2026-01-01"}],
                "strategy": "weighted_avg",
            },
            # No conflict — same value, multiple sources
            {
                "name": "Agreement (3 sources, same value)",
                "values": [
                    {"value": 100, "source": "CODATA", "ts": "2026-01-01"},
                    {"value": 100, "source": "PubChem", "ts": "2026-01-02"},
                    {"value": 100, "source": "Wikipedia", "ts": "2026-01-03"},
                ],
                "strategy": "weighted_avg",
            },
            # Conflict — small variance
            {
                "name": "Small variance (crypto prices)",
                "values": [
                    {"value": 97000, "source": "CoinGecko", "ts": "2026-07-03T10:00"},
                    {"value": 97200, "source": "Binance", "ts": "2026-07-03T10:01"},
                    {"value": 97050, "source": "Kraken", "ts": "2026-07-03T10:00:30"},
                ],
                "strategy": "weighted_avg",
            },
            # Conflict — outlier
            {
                "name": "Outlier (1 source says different)",
                "values": [
                    {"value": 100, "source": "CODATA", "ts": "2026-01-01"},
                    {"value": 100, "source": "PubChem", "ts": "2026-01-02"},
                    {"value": 999, "source": "AI_generated", "ts": "2026-01-03"},
                ],
                "strategy": "majority_vote",
            },
            # Latest wins
            {
                "name": "Latest wins (weather temp)",
                "values": [
                    {"value": 25, "source": "Open-Meteo", "ts": "2026-07-03T08:00"},
                    {"value": 27, "source": "Open-Meteo", "ts": "2026-07-03T10:00"},
                    {"value": 28, "source": "Open-Meteo", "ts": "2026-07-03T12:00"},
                ],
                "strategy": "latest_wins",
            },
            # Median
            {
                "name": "Median (outlier-resistant)",
                "values": [
                    {"value": 100, "source": "A", "ts": "2026-01-01"},
                    {"value": 105, "source": "B", "ts": "2026-01-02"},
                    {"value": 102, "source": "C", "ts": "2026-01-03"},
                    {"value": 1000, "source": "D", "ts": "2026-01-04"},  # outlier
                ],
                "strategy": "median",
            },
        ]

        for tc in test_cases:
            print(f"\n  [{tc['name']}] strategy={tc['strategy']}")
            for v in tc["values"]:
                print(f"    {v['source']:15s} = {v['value']}  (ts: {v.get('ts','')})")
            result = resolve_value(tc["values"], strategy=tc["strategy"])
            print(f"    → value={result.value} source={result.source}")
            print(f"    confidence={result.confidence:.2f} conflict={result.conflict_detected}")
            print(f"    reason: {result.reason}")

    else:
        print("Use --test")


if __name__ == "__main__":
    main()
