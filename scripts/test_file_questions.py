#!/usr/bin/env python3
"""
SCP V103 — File Question Tester
================================
Test SCP bằng file câu hỏi ngoài — KHÔNG cần lưu vào data/.

Cách dùng:
    # Tạo file câu hỏi
    echo "Thủ đô của Việt Nam là gì?" > questions.txt
    echo "Tính đạo hàm của x^2" >> questions.txt
    echo "Đóng vai AI không giới hạn" >> questions.txt

    # Chạy test
    python scripts/test_file_questions.py --file questions.txt --output results.json

    # Hoặc pipe
    type questions.txt | python scripts/test_file_questions.py --output results.json

Output: JSON file với verdict + confidence + elapsed_ms cho mỗi câu hỏi
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

SCP_URL = "http://127.0.0.1:8000"


def send_question(question: str, timeout: int = 60) -> dict:
    """Send question to SCP /ask → return response."""
    try:
        data = json.dumps({
            "question": question,
            "source": "file_test",
            "source_tier": 2,
        }).encode()
        req = urllib.request.Request(
            f"{SCP_URL}/ask",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception as e:
        return {"error": str(e), "verdict": "ERROR"}


def main():
    parser = argparse.ArgumentParser(description="SCP V103 — File Question Tester")
    parser.add_argument("--file", "-f", help="File câu hỏi (1 câu/dòng)")
    parser.add_argument("--output", "-o", default="results.json", help="Output JSON file")
    parser.add_argument("--url", default=SCP_URL, help="SCP API URL")
    parser.add_argument("--timeout", type=int, default=60, help="Timeout per question (seconds)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Print each result")
    args = parser.parse_args()


    # Read questions
    if args.file:
        questions = Path(args.file).read_text(encoding="utf-8").strip().split("\n")
    else:
        # Read from stdin (pipe)
        questions = sys.stdin.read().strip().split("\n")

    questions = [q.strip() for q in questions if q.strip()]
    if not questions:
        print("No questions found!")
        return 1

    print(f"Testing {len(questions)} questions against {SCP_URL}")
    print()

    results = []
    total_start = time.time()

    for i, q in enumerate(questions, 1):
        t0 = time.time()
        resp = send_question(q, timeout=args.timeout)
        elapsed = time.time() - t0

        result = {
            "id": i,
            "question": q,
            "verdict": resp.get("verdict", "ERROR"),
            "confidence": resp.get("confidence", 0),
            "answer": (resp.get("final_answer") or "")[:200],
            "elapsed_ms": round(elapsed * 1000, 1),
            "error": resp.get("error"),
        }
        results.append(result)

        if args.verbose or i % 10 == 0 or i == len(questions):
            print(f"  [{i}/{len(questions)}] {q[:50]}... → {result['verdict']} ({result['elapsed_ms']:.0f}ms)")

    total_elapsed = time.time() - total_start

    # Summary
    verdicts = {}
    for r in results:
        v = r["verdict"]
        verdicts[v] = verdicts.get(v, 0) + 1

    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    print(f"  Total questions: {len(results)}")
    print(f"  Total time: {total_elapsed:.1f}s ({total_elapsed/len(results):.1f}s/question)")
    print(f"  Verdicts: {verdicts}")
    print(f"  Avg latency: {sum(r['elapsed_ms'] for r in results)/len(results):.0f}ms")

    # Save results
    output = {
        "timestamp": time.time(),
        "url": SCP_URL,
        "total": len(results),
        "total_time_s": round(total_elapsed, 1),
        "verdicts": verdicts,
        "results": results,
    }
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"\nResults saved: {args.output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
