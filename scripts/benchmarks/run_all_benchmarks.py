#!/usr/bin/env python3
"""
SCP V98 — Benchmark Runner
============================
Chạy 4 benchmark chuẩn: MMLU, GPQA, MATH, HumanEval.

Cách chạy:
    # Quick mode (10 questions each, không cần SCP chạy)
    python scripts/benchmarks/run_all_benchmarks.py --quick

    # Full mode (100+ questions, cần SCP service chạy tại localhost:7860)
    python scripts/benchmarks/run_all_benchmarks.py --full --scp-url http://localhost:7860

    # Chỉ 1 benchmark
    python scripts/benchmarks/run_all_benchmarks.py --only mmlu
    python scripts/benchmarks/run_all_benchmarks.py --only math --scp-url http://localhost:7860

Output: JSON report trong EVALUATION/benchmark_results_YYYYMMDD_HHMMSS.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

SCP_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(SCP_ROOT))

BENCHMARK_DIR = Path(__file__).resolve().parent
SAMPLE_DIR = BENCHMARK_DIR / "sample_data"
OUTPUT_DIR = SCP_ROOT / "EVALUATION"
OUTPUT_DIR.mkdir(exist_ok=True)


def load_sample(name: str) -> list[dict]:
    """Load sample questions từ sample_data/{name}.json"""
    path = SAMPLE_DIR / f"{name}.json"
    if not path.exists():
        print(f"⚠ Sample file not found: {path}")
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def call_scp(question: str, scp_url: str, timeout: int = 30) -> dict:
    """Gọi SCP /ask endpoint."""
    try:
        import httpx
    except ImportError:
        # Fallback to urllib
        import urllib.error
        import urllib.request
        data = json.dumps({"question": question, "timeout": timeout}).encode()
        req = urllib.request.Request(
            f"{scp_url}/ask",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout + 5) as resp:
                return json.loads(resp.read())
        except urllib.error.URLError as e:
            return {"error": str(e), "verdict": "ERROR"}
    try:
        with httpx.Client(timeout=timeout + 5) as client:
            r = client.post(f"{scp_url}/ask", json={"question": question, "timeout": timeout})
            r.raise_for_status()
            return r.json()
    except Exception as e:
        return {"error": str(e), "verdict": "ERROR"}


def call_baseline_llm(question: str, choices: list[str] | None = None) -> dict:
    """
    Baseline LLM call — dùng OpenAI API nếu có OPENAI_API_KEY.
    Nếu không có, return 'SKIP' để bỏ qua baseline comparison.
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return {"answer": "SKIP", "verdict": "SKIP"}

    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        prompt = question
        if choices:
            prompt = f"{question}\n\nChoices: {chr(10).join(f'{chr(65+i)}. {c}' for i, c in enumerate(choices))}\n\nAnswer with just the letter (A, B, C, or D):"
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=200,
            temperature=0.0,
        )
        return {"answer": resp.choices[0].message.content.strip(), "verdict": "BASELINE"}
    except Exception as e:
        return {"answer": f"ERROR: {e}", "verdict": "ERROR"}


# ============================================================
# MMLU Benchmark
# ============================================================
def run_mmlu(scp_url: str | None, quick: bool = False) -> dict:
    """MMLU — Multiple choice questions across 57 subjects."""
    print("\n" + "="*60)
    print("BENCHMARK: MMLU (Massive Multitask Language Understanding)")
    print("="*60)

    questions = load_sample("mmlu_sample")
    if quick:
        questions = questions[:10]
    print(f"Loaded {len(questions)} MMLU questions")

    correct_scp = 0
    correct_baseline = 0
    total = 0
    errors = 0
    details = []

    for i, q in enumerate(questions):
        print(f"  [{i+1}/{len(questions)}] {q['subject']}: {q['question'][:60]}...")
        # SCP
        scp_resp = {"answer": "SKIP", "verdict": "SKIP"}
        if scp_url:
            scp_resp = call_scp(q["question"] + " Choose the best answer.", scp_url)
            scp_answer = scp_resp.get("answer", "").strip().upper()
            scp_correct = scp_answer.startswith(q["answer"]) or q["answer"] in scp_answer
        else:
            scp_correct = None
            scp_answer = "SKIP"

        # Baseline
        baseline_resp = call_baseline_llm(q["question"], q.get("choices"))
        baseline_answer = baseline_resp.get("answer", "").strip().upper()
        baseline_correct = baseline_answer.startswith(q["answer"])

        if scp_correct:
            correct_scp += 1
        if baseline_correct:
            correct_baseline += 1
        total += 1
        if "error" in scp_resp:
            errors += 1

        details.append({
            "id": q.get("id", i),
            "subject": q["subject"],
            "question": q["question"],
            "expected": q["answer"],
            "scp_answer": scp_answer,
            "scp_correct": scp_correct,
            "baseline_answer": baseline_answer,
            "baseline_correct": baseline_correct,
        })

    return {
        "benchmark": "MMLU",
        "total_questions": total,
        "scp_correct": correct_scp,
        "scp_accuracy": round(correct_scp / total, 4) if total > 0 else 0,
        "baseline_correct": correct_baseline,
        "baseline_accuracy": round(correct_baseline / total, 4) if total > 0 else 0,
        "scp_improvement": round((correct_scp - correct_baseline) / total * 100, 2) if total > 0 else 0,
        "errors": errors,
        "details": details,
    }


# ============================================================
# GPQA Benchmark
# ============================================================
def run_gpqa(scp_url: str | None, quick: bool = False) -> dict:
    """GPQA — Google-Proof Q&A (post-graduate science)."""
    print("\n" + "="*60)
    print("BENCHMARK: GPQA (Google-Proof Q&A)")
    print("="*60)

    questions = load_sample("gpqa_sample")
    if quick:
        questions = questions[:5]
    print(f"Loaded {len(questions)} GPQA questions")

    correct_scp = 0
    correct_baseline = 0
    total = 0
    details = []

    for i, q in enumerate(questions):
        print(f"  [{i+1}/{len(questions)}] {q['domain']}: {q['question'][:60]}...")
        if scp_url:
            scp_resp = call_scp(q["question"], scp_url, timeout=60)
            scp_answer = scp_resp.get("answer", "").strip().lower()
            scp_correct = q["answer"].lower() in scp_answer
        else:
            scp_correct = None

        baseline_resp = call_baseline_llm(q["question"], q.get("choices"))
        baseline_answer = baseline_resp.get("answer", "").strip().lower()
        baseline_correct = q["answer"].lower() in baseline_answer

        if scp_correct:
            correct_scp += 1
        if baseline_correct:
            correct_baseline += 1
        total += 1

        details.append({
            "id": q.get("id", i),
            "domain": q["domain"],
            "question": q["question"],
            "expected": q["answer"],
            "scp_correct": scp_correct,
            "baseline_correct": baseline_correct,
        })

    return {
        "benchmark": "GPQA",
        "total_questions": total,
        "scp_correct": correct_scp,
        "scp_accuracy": round(correct_scp / total, 4) if total > 0 else 0,
        "baseline_correct": correct_baseline,
        "baseline_accuracy": round(correct_baseline / total, 4) if total > 0 else 0,
        "details": details,
    }


# ============================================================
# MATH Benchmark
# ============================================================
def run_math(scp_url: str | None, quick: bool = False) -> dict:
    """MATH — Competition math problems."""
    print("\n" + "="*60)
    print("BENCHMARK: MATH (Competition Math)")
    print("="*60)

    questions = load_sample("math_sample")
    if quick:
        questions = questions[:5]
    print(f"Loaded {len(questions)} MATH questions")

    correct_scp = 0
    correct_baseline = 0
    total = 0
    details = []

    for i, q in enumerate(questions):
        print(f"  [{i+1}/{len(questions)}] Level {q.get('level', '?')}: {q['question'][:60]}...")
        if scp_url:
            scp_resp = call_scp(q["question"] + " Show your work and provide the final answer.", scp_url, timeout=60)
            scp_answer = scp_resp.get("answer", "").strip()
            # MATH answers are usually \boxed{...}
            scp_correct = q["answer"] in scp_answer
        else:
            scp_correct = None

        baseline_resp = call_baseline_llm(q["question"] + " Provide the final answer.")
        baseline_answer = baseline_resp.get("answer", "").strip()
        baseline_correct = q["answer"] in baseline_answer

        if scp_correct:
            correct_scp += 1
        if baseline_correct:
            correct_baseline += 1
        total += 1

        details.append({
            "id": q.get("id", i),
            "level": q.get("level"),
            "question": q["question"],
            "expected": q["answer"],
            "scp_correct": scp_correct,
            "baseline_correct": baseline_correct,
        })

    return {
        "benchmark": "MATH",
        "total_questions": total,
        "scp_correct": correct_scp,
        "scp_accuracy": round(correct_scp / total, 4) if total > 0 else 0,
        "baseline_correct": correct_baseline,
        "baseline_accuracy": round(correct_baseline / total, 4) if total > 0 else 0,
        "details": details,
    }


# ============================================================
# HumanEval Benchmark
# ============================================================
def run_humaneval(scp_url: str | None, quick: bool = False) -> dict:
    """HumanEval — Python code generation."""
    print("\n" + "="*60)
    print("BENCHMARK: HumanEval (Python Code Generation)")
    print("="*60)

    questions = load_sample("humaneval_sample")
    if quick:
        questions = questions[:5]
    print(f"Loaded {len(questions)} HumanEval problems")

    correct_scp = 0
    correct_baseline = 0
    total = 0
    details = []

    for i, q in enumerate(questions):
        print(f"  [{i+1}/{len(questions)}] Task {q.get('task_id', i)}: {q['prompt'][:60]}...")
        prompt = f"Complete this Python function:\n\n{q['prompt']}\n\nProvide only the function body (no explanation):"

        if scp_url:
            scp_resp = call_scp(prompt, scp_url, timeout=60)
            scp_code = scp_resp.get("answer", "")
            # Try to execute the test
            scp_correct = _test_python_code(q, scp_code)
        else:
            scp_correct = None
            scp_code = "SKIP"

        baseline_resp = call_baseline_llm(prompt)
        baseline_code = baseline_resp.get("answer", "")
        baseline_correct = _test_python_code(q, baseline_code)

        if scp_correct:
            correct_scp += 1
        if baseline_correct:
            correct_baseline += 1
        total += 1

        details.append({
            "task_id": q.get("task_id", i),
            "prompt": q["prompt"][:200],
            "scp_correct": scp_correct,
            "baseline_correct": baseline_correct,
        })

    return {
        "benchmark": "HumanEval",
        "total_questions": total,
        "scp_correct": correct_scp,
        "scp_accuracy": round(correct_scp / total, 4) if total > 0 else 0,
        "baseline_correct": correct_baseline,
        "baseline_accuracy": round(correct_baseline / total, 4) if total > 0 else 0,
        "details": details,
    }


def _test_python_code(task: dict, code: str) -> bool:
    """Thử chạy code + test cases. Return True nếu pass tất cả tests."""
    if not code or code == "SKIP":
        return False
    # Sandbox: chạy trong subprocess với timeout
    import subprocess
    full_code = code + "\n\n" + task.get("test", "") + "\n\n" + task.get("entry_point", "check") + "()"
    try:
        result = subprocess.run(
            ["python3", "-c", full_code],
            capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        return False
    except Exception:
        return False


# ============================================================
# Main
# ============================================================
def main():
    parser = argparse.ArgumentParser(description="SCP V98 Benchmark Runner")
    parser.add_argument("--quick", action="store_true", help="Quick mode (5-10 questions each)")
    parser.add_argument("--full", action="store_true", help="Full mode (100+ questions each)")
    parser.add_argument("--only", choices=["mmlu", "gpqa", "math", "humaneval"], help="Run only one benchmark")
    parser.add_argument("--scp-url", default=None, help="SCP API URL (e.g., http://localhost:7860). If omitted, only baseline is run.")
    parser.add_argument("--output", default=None, help="Output JSON file path")
    args = parser.parse_args()

    print("SCP V98 Benchmark Runner")
    print(f"  Mode: {'quick' if args.quick else 'full' if args.full else 'default'}")
    print(f"  SCP URL: {args.scp_url or '(not set — baseline only)'}")
    print(f"  Time: {datetime.now().isoformat()}")

    if not args.scp_url:
        print("\n⚠ No --scp-url provided. Running baseline-only mode.")
        print("  To run SCP + baseline comparison, start SCP and pass --scp-url http://localhost:7860")

    results = {
        "timestamp": datetime.now().isoformat(),
        "scp_url": args.scp_url,
        "mode": "quick" if args.quick else "full" if args.full else "default",
        "benchmarks": {},
    }

    benchmarks_to_run = [args.only] if args.only else ["mmlu", "gpqa", "math", "humaneval"]
    runners = {
        "mmlu": run_mmlu,
        "gpqa": run_gpqa,
        "math": run_math,
        "humaneval": run_humaneval,
    }

    for name in benchmarks_to_run:
        try:
            results["benchmarks"][name] = runners[name](args.scp_url, quick=args.quick)
        except Exception as e:
            print(f"❌ Error in {name}: {e}")
            results["benchmarks"][name] = {"error": str(e)}

    # Summary
    print("\n" + "="*60)
    print("BENCHMARK SUMMARY")
    print("="*60)
    print(f"{'Benchmark':<12} {'SCP':<10} {'Baseline':<10} {'Δ':<10}")
    print("-" * 42)
    for name, r in results["benchmarks"].items():
        if "error" in r:
            print(f"{name:<12} ERROR: {r['error']}")
            continue
        scp_acc = r.get("scp_accuracy", 0) * 100
        base_acc = r.get("baseline_accuracy", 0) * 100
        delta = scp_acc - base_acc
        print(f"{name:<12} {scp_acc:>6.1f}%   {base_acc:>6.1f}%   {delta:>+6.1f}pp")

    # Save
    if args.output:
        out_path = args.output
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = str(OUTPUT_DIR / f"benchmark_results_{timestamp}.json")

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\n✓ Results saved to: {out_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
