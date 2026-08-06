"""
[Task 8-A] Import endpoints — extracted from api_server.py

TẠI SAO: api_server.py god file. Tách 3 routes /import/* vào module này.
Backward-compatible — public API paths/methods unchanged.

Routes:
  POST /import/jsonl   — Import questions from JSONL file (1 JSON per line)
  POST /import/excel   — Import questions from Excel/CSV file
  POST /import/batch   — Import batch of questions as JSON array
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

# Import shared deps from api_server (same pattern as api/chat.py + admin_v98.py)
from scp.api._shared import get_judge, verify_admin

router = APIRouter(tags=["import"])


@router.post("/import/jsonl")
async def import_jsonl(request: Request, _admin: bool = Depends(verify_admin)):
    """Import questions from JSONL file.

    JSONL format (1 JSON per line):
      {"question": "...", "ai_answer": "...", "domain": "..."}
      {"question": "...", "ai_answer": "...", "domain": "..."}

    Returns: List of verdicts for each question.
    """
    import json as _json
    body = await request.body()
    lines = body.decode("utf-8").strip().split("\n")

    judge = get_judge()
    results = []

    for i, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            data = _json.loads(line)
            question = data.get("question", "")
            ai_answer = data.get("ai_answer", data.get("answer", ""))
            data.get("domain", "general")

            v = judge.judge(question=question, ai_answer=ai_answer, cycle_count=0)
            results.append({
                "line": i + 1,
                "question": question[:100],
                "verdict": v.verdict,
                "confidence": v.confidence,
                "falsification": v.evidence.get("falsification_status"),
                "governance": v.evidence.get("governance_decision"),
                "elapsed_ms": v.evidence.get("v100_phase_timings", {}).get("total_ms", 0),
            })
        except Exception as e:
            results.append({"line": i + 1, "error": str(e)})

    summary = {
        "total": len(results),
        "pass": sum(1 for r in results if r.get("verdict") == "PASS"),
        "fail": sum(1 for r in results if r.get("verdict") == "FAIL"),
        "unknown": sum(1 for r in results if r.get("verdict") == "UNKNOWN"),
        "errors": sum(1 for r in results if "error" in r),
    }

    return {"summary": summary, "results": results}


@router.post("/import/excel")
async def import_excel(request: Request, _admin: bool = Depends(verify_admin)):
    """Import questions from Excel file (CSV format).

    CSV format:
      question,ai_answer,domain
      "Tính 2+3?","5","math"
      "Thủ đô VN?","Hà Nội","geography"

    Returns: List of verdicts for each question.
    """
    import csv
    import io

    body = await request.body()
    text = body.decode("utf-8-sig")  # handle BOM
    reader = csv.DictReader(io.StringIO(text))

    judge = get_judge()
    results = []

    for i, row in enumerate(reader, 1):
        try:
            question = row.get("question", row.get("Question", ""))
            ai_answer = row.get("ai_answer", row.get("answer", row.get("Answer", "")))
            row.get("domain", row.get("Domain", "general"))

            if not question:
                continue

            v = judge.judge(question=question, ai_answer=ai_answer, cycle_count=0)
            results.append({
                "row": i,
                "question": question[:100],
                "verdict": v.verdict,
                "confidence": v.confidence,
                "falsification": v.evidence.get("falsification_status"),
                "governance": v.evidence.get("governance_decision"),
                "elapsed_ms": v.evidence.get("v100_phase_timings", {}).get("total_ms", 0),
            })
        except Exception as e:
            results.append({"row": i, "error": str(e)})

    summary = {
        "total": len(results),
        "pass": sum(1 for r in results if r.get("verdict") == "PASS"),
        "fail": sum(1 for r in results if r.get("verdict") == "FAIL"),
        "unknown": sum(1 for r in results if r.get("verdict") == "UNKNOWN"),
        "errors": sum(1 for r in results if "error" in r),
    }

    return {"summary": summary, "results": results}


@router.post("/import/batch")
async def import_batch(request: Request, _admin: bool = Depends(verify_admin)):
    """Import batch of questions as JSON array.

    Body:
      [
        {"question": "...", "ai_answer": "...", "domain": "..."},
        {"question": "...", "ai_answer": "...", "domain": "..."}
      ]

    Returns: List of verdicts.
    """
    body = await request.json()

    judge = get_judge()
    results = []

    for i, item in enumerate(body, 1):
        try:
            question = item.get("question", "")
            ai_answer = item.get("ai_answer", item.get("answer", ""))
            item.get("domain", "general")

            v = judge.judge(question=question, ai_answer=ai_answer, cycle_count=0)
            results.append({
                "item": i,
                "question": question[:100],
                "verdict": v.verdict,
                "confidence": v.confidence,
                "falsification": v.evidence.get("falsification_status"),
                "governance": v.evidence.get("governance_decision"),
                "elapsed_ms": v.evidence.get("v100_phase_timings", {}).get("total_ms", 0),
            })
        except Exception as e:
            results.append({"item": i, "error": str(e)})

    summary = {
        "total": len(results),
        "pass": sum(1 for r in results if r.get("verdict") == "PASS"),
        "fail": sum(1 for r in results if r.get("verdict") == "FAIL"),
        "unknown": sum(1 for r in results if r.get("verdict") == "UNKNOWN"),
    }

    return {"summary": summary, "results": results}
