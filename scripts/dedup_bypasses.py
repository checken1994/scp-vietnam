#!/usr/bin/env python3
"""
[V5.8-OPT] One-time cleanup script — dedup bypasses.jsonl.

TẠI SAO: bypasses.jsonl ballooned to 6.8 MB / 47686 lines because the
attack_crawler + cross_language_learner generate 8 language variants
(en/zh/ja/ko/fr/de/es/ar) of every attack pattern. ~6000 unique logical
attacks × 8 = ~48000 lines, of which ~42000 are duplicates.

Dedup rules (mirror record_bypass() in scp/security/attack_memory.py):
  1. Same attack_type + same question[:200] → duplicate (keep first)
  2. cross_lang_<non-en> with same question as an existing cross_lang_en
     → translated_variant (skip — canonical English already kept)

Usage:
    python scripts/dedup_bypasses.py [--input data/bypasses.jsonl] [--dry-run] [--backup]

Default: reads data/bypasses.jsonl, writes deduped version in-place,
         creates data/bypasses.jsonl.bak if --backup (default ON for safety).
         Use --dry-run to preview without writing.

Exit codes:
    0  success (or dry-run)
    1  input file not found / unreadable
    2  write error
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any


def _hash(attack_type: str, question: str) -> str:
    """Mirror the hash used in AttackPatternMemory.record_bypass.

    [SECURITY-FIX] B324: was hashlib.md5 (weak hash). Now SHA-256.
    Note: This hash is for DEDUPLICATION (collision avoidance), NOT security.
    But SHA-256 costs nothing and silences the warning properly.
    """
    return hashlib.sha256(
        f"{attack_type}:{(question or '')[:200]}".encode()
    ).hexdigest()


def dedup_file(
    input_path: Path,
    output_path: Path | None = None,
    dry_run: bool = False,
    backup: bool = True,
) -> dict[str, int]:
    """Dedup a bypasses.jsonl file.

    Returns stats dict: {original, kept, duplicates_dropped, translated_variants_dropped}
    """
    if not input_path.is_file():
        print(f"ERROR: input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    # Pass 1: read all lines
    original_count = 0
    records: list[dict[str, Any]] = []
    bad_lines = 0
    try:
        with input_path.open("r", encoding="utf-8") as f:
            for line in f:
                original_count += 1
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    bad_lines += 1
                    # Keep malformed lines as-is (don't lose them)
                    records.append({"_raw": line, "_malformed": True})
    except Exception as e:
        print(f"ERROR: failed to read {input_path}: {e}", file=sys.stderr)
        sys.exit(1)

    # Pass 2: dedup
    seen_hashes: set[str] = set()
    seen_canonical_hashes: set[str] = set()  # cross_lang_en canonicals
    kept: list[dict[str, Any]] = []
    duplicates_dropped = 0
    translated_variants_dropped = 0
    malformed_kept = 0

    for rec in records:
        # Preserve malformed lines (don't drop data we can't parse)
        if rec.get("_malformed"):
            kept.append(rec)
            malformed_kept += 1
            continue

        attack_type = rec.get("attack_type", "") or ""
        question = rec.get("question", "") or ""

        # cross_lang_<non-en> → check canonical (en) hash
        if (
            attack_type.startswith("cross_lang_")
            and attack_type != "cross_lang_en"
        ):
            canon_hash = _hash("cross_lang_en", question)
            if canon_hash in seen_canonical_hashes:
                translated_variants_dropped += 1
                continue
            # NOTE: we don't add canon_hash here — the canonical en record
            # (if it exists) will add it. If no en record exists, we KEEP
            # the non-en variant as the canonical for this question.

        h = _hash(attack_type, question)
        if h in seen_hashes:
            duplicates_dropped += 1
            continue
        seen_hashes.add(h)

        # Track canonical en hashes for cross_lang dedup
        if attack_type == "cross_lang_en":
            seen_canonical_hashes.add(h)

        kept.append(rec)

    stats = {
        "original": original_count,
        "kept": len(kept),
        "duplicates_dropped": duplicates_dropped,
        "translated_variants_dropped": translated_variants_dropped,
        "malformed_kept": malformed_kept,
    }

    # Print report
    print(f"\n[V5.8-DEDUP] Report for {input_path}")
    print(f"  Original lines:              {stats['original']:>8}")
    print(f"  Kept (unique):               {stats['kept']:>8}")
    print(f"  Duplicates dropped:          {stats['duplicates_dropped']:>8}")
    print(f"  Translated variants dropped: {stats['translated_variants_dropped']:>8}")
    if stats["malformed_kept"]:
        print(f"  Malformed (kept as-is):      {stats['malformed_kept']:>8}")
    reduction = (
        (stats["original"] - stats["kept"]) / stats["original"] * 100
        if stats["original"] > 0
        else 0
    )
    print(f"  Reduction:                   {reduction:>7.1f}%")

    if dry_run:
        print("\n[DRY-RUN] No files written. Re-run without --dry-run to apply.")
        return stats

    # Pass 3: write deduped file
    if output_path is None:
        output_path = input_path

    if backup and input_path == output_path and input_path.exists():
        backup_path = input_path.with_suffix(input_path.suffix + ".bak")
        # Don't overwrite existing backup
        if not backup_path.exists():
            print(f"\n[V5.8-DEDUP] Creating backup: {backup_path}")
            try:
                # Use copyfile + preserve contents (atomic via temp + replace)
                import shutil
                shutil.copy2(input_path, backup_path)
            except Exception as e:
                print(f"ERROR: backup failed: {e}", file=sys.stderr)
                sys.exit(2)

    # Atomic write: temp file + os.replace
    tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    try:
        with tmp_path.open("w", encoding="utf-8") as f:
            for rec in kept:
                if rec.get("_malformed"):
                    f.write(rec.get("_raw", "") + "\n")
                else:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        os.replace(tmp_path, output_path)
    except Exception as e:
        print(f"ERROR: write failed: {e}", file=sys.stderr)
        # Clean up temp file if it exists
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass
        sys.exit(2)

    in_size = input_path.stat().st_size if input_path.exists() else 0
    out_size = output_path.stat().st_size if output_path.exists() else 0
    print(f"\n[V5.8-DEDUP] Wrote: {output_path}")
    print(f"  Size: {in_size:,} → {out_size:,} bytes "
          f"({(in_size - out_size) / in_size * 100:.1f}% reduction)" if in_size > 0 else "")

    return stats


def main() -> int:
    parser = argparse.ArgumentParser(
        description="[V5.8-OPT] Dedup bypasses.jsonl (one-time cleanup)",
    )
    parser.add_argument(
        "--input", "-i",
        default="data/bypasses.jsonl",
        help="Input file path (default: data/bypasses.jsonl)",
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="Output file path (default: in-place)",
    )
    parser.add_argument(
        "--dry-run", "-n",
        action="store_true",
        help="Preview only — don't write any files",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Skip creating .bak file (default: backup ON for in-place writes)",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output) if args.output else None

    print("[V5.8-OPT] dedup_bypasses.py — one-time bypasses.jsonl cleanup")
    print(f"  Mode:     {'DRY-RUN' if args.dry_run else 'WRITE'}")
    print(f"  Input:    {input_path}")
    print(f"  Output:   {output_path or '(in-place)'}")
    print(f"  Backup:   {'NO' if args.no_backup else 'YES (if in-place)'}")
    print(f"  Started:  {time.strftime('%Y-%m-%d %H:%M:%S')}")

    stats = dedup_file(
        input_path=input_path,
        output_path=output_path,
        dry_run=args.dry_run,
        backup=not args.no_backup,
    )

    print(f"\n[V5.8-OPT] Done. Removed {stats['duplicates_dropped'] + stats['translated_variants_dropped']} duplicates, kept {stats['kept']} unique.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
