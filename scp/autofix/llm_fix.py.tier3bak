"""
SCP LLM-Powered Fix Generator — lets SCP fix itself using LLM.

When AST scanner finds a bug with only text description (no code patch),
this module calls OpenRouter LLM to generate a proper search-replace fix
block that AutoFix can apply.

Flow:
  1. BugReport (text description, no code patch)
  2. Read buggy file + 5 lines context around bug line
  3. Send to LLM: "Fix this bug. Output <<<<<<< OLD / ======= / >>>>>>> NEW block."
  4. Parse LLM response, extract search-replace block
  5. AutoFix._apply_fix() applies the patch
  6. Verify: ast.parse + (optional) run pytest on affected file

This is the missing piece that makes "SCP tự fix SCP" actually work.
Before this, AutoFix reported "fixed" but patched=False (Bug #23).
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

logger = logging.getLogger("scp.autofix.llm_fix")

# Rate limit LLM calls (avoid burning OpenRouter quota)
# [V5.5-FIX] TĂI SAO: was 20 — quá thấp. 50 bugs/scan × 1 LLM call = 50 calls,
# nhưng rate limit 20/giờ → 30 calls bị skip → 30 bugs "LLM fix generation failed"
# → STARTUP-GATE block. Tăng lên 100/giờ (đủ cho 2 full scans).
# NOTE: BareExceptPass bugs giờ KHÔNG cần LLM (xem _generate_bare_except_fix below),
# nên LLM chỉ dùng cho bugs phức tạp → 100/giờ dư.
_MAX_LLM_FIXES_PER_HOUR = 100
_recent_llm_calls: list[float] = []


def _check_rate_limit() -> bool:
    """Return True if we can make another LLM call."""
    global _recent_llm_calls
    now = time.time()
    _recent_llm_calls = [t for t in _recent_llm_calls if now - t < 3600]
    if len(_recent_llm_calls) >= _MAX_LLM_FIXES_PER_HOUR:
        return False
    _recent_llm_calls.append(now)
    return True


# [V5.5-FIX] BareExceptPass pattern fixer — KHÔNG cần LLM
# TẠI SAO: 50 bugs trong SCP đều là `except Exception as e: pass` — pattern CỨNG.
# Gọi LLM cho mỗi cái = lãng phí (50 API calls, $0.000825, 30s latency).
# Fix trực tiếp bằng regex: thay `pass` bằng `logger.exception(...)`.
# Pattern an toàn: không thay đổi logic, chỉ thêm logging.
import re as _re_module

_BARE_EXCEPT_PASS_PATTERN = _re_module.compile(
    r'^(\s*)(except\s+\w*(?:\s+as\s+\w+)?\s*:\s*\n)(\s*)pass\s*$',
    _re_module.MULTILINE
)


# ============================================================
# [OPT-30] Improved prompt — clearer SEARCH/REPLACE format + few-shot examples
# WHY: 7/15 LLM calls produced "No search-replace block found" error.
# Root cause: Ollama (weak LLM) doesn't follow format without explicit examples.
# Fix: add few-shot examples + clearer rules + canonical SEARCH/REPLACE markers.
# DNA SCP #2 PASS ≠ ĐÚNG — the old prompt's prose format instructions were
# insufficient; weak LLMs (Ollama 7B/13B) need explicit examples to follow.
# DNA SCP #9 No harm — parser accepts BOTH old (OLD/NEW) and new (SEARCH/REPLACE)
# markers, so any in-flight fixes (LLM mid-call) still parse correctly.
# ============================================================

FIX_SYSTEM_PROMPT = """You are a Python code fixer. Your task is to fix bugs using SEARCH/REPLACE blocks.

## FORMAT (MUST FOLLOW EXACTLY)

<<<<<<< SEARCH
[exact code to find - including indentation]
=======
[fixed code - including indentation]
>>>>>>> REPLACE

## RULES
1. Output ONLY the SEARCH/REPLACE block — no explanations before/after.
2. SEARCH must match the EXACT code in the file (including spaces/indentation).
3. REPLACE must be valid Python (preserves indentation).
4. Do NOT add imports unless absolutely necessary.
5. If you cannot fix the bug, output exactly: NO_FIX_POSSIBLE

## EXAMPLE

Bug: BareExceptPass at line 5
File code:
    try:
        do_something()
    except Exception as e:
        pass

Correct output:
<<<<<<< SEARCH
    except Exception as e:
        pass
=======
    except Exception as e:
        logger.warning(repr(e))
>>>>>>> REPLACE

## YOUR TASK

Bug type: {bug_type}
Description: {bug_description}
File: {file_path}
Line: {line_number}

Code context (line numbers shown as `# line N >>>` comments — the `>>>` marker shows the buggy line; do NOT include these comments in your SEARCH block):
{code_context}

Generate the SEARCH/REPLACE block to fix this bug:"""


def _build_fix_prompt(bug, code_context: str) -> str:
    """Build improved prompt with few-shot example.

    [OPT-30] WHY: replaces the inline prose prompt that had 7/15 LLM failures.
    Now uses FIX_SYSTEM_PROMPT template with explicit rules + worked example.
    """
    return FIX_SYSTEM_PROMPT.format(
        bug_type=bug.bug_type,
        bug_description=getattr(bug, 'description', 'N/A'),
        file_path=bug.file,
        line_number=bug.line,
        code_context=code_context,
    )


def _generate_bare_except_fix(bug) -> str | None:
    """Generate fix for BareExceptPass bugs WITHOUT calling LLM.

    Pattern: `except Exception as e: pass` → `except Exception as e: logger.exception("...")`

    Returns search-replace block, or None if not a BareExceptPass bug.
    """
    if bug.bug_type != "BareExceptPass":
        return None

    filepath = Path(bug.file)
    if not filepath.exists():
        return None

    try:
        source = filepath.read_text(encoding="utf-8")
        lines = source.splitlines()
        bug_line_idx = bug.line - 1  # 0-indexed
        if bug_line_idx >= len(lines):
            return None

        # Find the `except ...:` line and the `pass` line
        except_line = lines[bug_line_idx].rstrip()
        # Check if this line is `except ...:`
        except_match = _re_module.match(
            r'^(\s*)(except\s+)(\w+)(\s+as\s+\w+)?\s*:\s*$',
            except_line
        )
        if not except_match:
            # Maybe the `pass` is on this line (single-line except: pass)
            # Try: `except Exception as e: pass`
            single_match = _re_module.match(
                r'^(\s*)(except\s+)(\w+)(\s+as\s+\w+)?\s*:\s*pass\s*$',
                except_line
            )
            if single_match:
                indent = single_match.group(1)
                except_kw = single_match.group(2)
                exc_type = single_match.group(3)
                # Single-line → multi-line with logger
                old_block = f"{indent}{except_kw}{exc_type}: pass"
                new_block = (
                    f"{indent}{except_kw}{exc_type} as e:\n"
                    f"{indent}    logger.exception(f\"[{filepath.name}:{bug.line}] silenced exception\")"
                )
                # [OPT-30] emit canonical SEARCH/REPLACE format
                return f"<<<<<<< SEARCH\n{old_block}\n=======\n{new_block}\n>>>>>>> REPLACE"
            return None

        # Multi-line: except on this line, pass on next
        indent = except_match.group(1)
        except_kw = except_match.group(2)
        exc_type = except_match.group(3)
        as_clause = except_match.group(4) or ""

        # Look for `pass` on next line(s)
        pass_line_idx = bug_line_idx + 1
        while pass_line_idx < len(lines):
            pass_line = lines[pass_line_idx].rstrip()
            if pass_line.strip() == "":
                pass_line_idx += 1
                continue
            if pass_line.strip() == "pass":
                # Found it
                pass_indent = _re_module.match(r'^(\s*)', pass_line).group(1)
                old_block = (
                    f"{indent}{except_kw}{exc_type}{as_clause}:\n"
                    f"{pass_indent}pass"
                )
                # If no `as e`, add it
                if not as_clause:
                    new_except = f"{except_kw}{exc_type} as e:"
                else:
                    new_except = f"{except_kw}{exc_type}{as_clause}:"
                new_block = (
                    f"{indent}{new_except}\n"
                    f"{pass_indent}logger.exception(f\"[{filepath.name}:{bug.line}] silenced exception\")"
                )
                # [OPT-30] emit canonical SEARCH/REPLACE format
                return f"<<<<<<< SEARCH\n{old_block}\n=======\n{new_block}\n>>>>>>> REPLACE"
            else:
                # Not a pass — maybe it's a different structure
                return None

        return None
    except Exception as e:
        logger.warning(f"[llm_fix] BareExceptPass fix generation failed: {e}")
        return None


# ============================================================
# [ROOT-FIX 44-A] MULTI-MODEL ROUTING — task="autofix" → deepseek-r1:8b
# ============================================================
# WHY: previously all AutoFix calls used 1 Ollama model (llama3.2:3B) →
# 63% rollback rate because 3B is too weak for code reasoning. Now the
# gateway routes task="autofix" to deepseek-r1:8b (user's strongest code
# reasoning model). Cloud fallback (DeepSeek/Anthropic/OpenAI) removed —
# user only uses Ollama + OpenRouter.
#
# [OPT-17] bug-complexity classification kept for STATS ONLY (logging).
# The actual routing is now done by the gateway via task="autofix" —
# deepseek-r1:8b handles ALL bug types well (including complex SQL injection,
# race conditions, etc.) so we don't need per-bug-type provider selection.
# ============================================================

# Bug complexity classification (for STATS logging only — gateway routes
# all AutoFix calls through deepseek-r1:8b regardless of complexity).
_SIMPLE_BUG_TYPES = {
    "BareExceptPass", "UnusedImport", "UnusedVariable",
    "PossiblyUndefinedName", "MissingDocstring",
}

_COMPLEX_BUG_TYPES = {
    "SQLInjection", "SQLInjectionRisk", "RaceCondition",
    "LogicFlow", "TypeContract", "SecurityIssue",
    "PathTraversal", "NullSafety", "XSSVulnerability",
}

_MEDIUM_BUG_TYPES = {
    "ResourceLeak", "DeadCode", "RoutingGap", "SchemaMismatch",
    "PerformanceIssue", "APIWiring",
}


def select_provider_for_bug(bug_type: str) -> str:
    """[ROOT-FIX 44-A] DEPRECATED — always returns "ollama".

    [OPT-17] Originally picked per-bug-type cloud provider (deepseek for
    medium, anthropic for complex, openrouter for unknown). Removed in 44-A
    because user has no DeepSeek/Anthropic/OpenAI API keys. All AutoFix
    calls now go through Ollama via gateway.chat_sync(task="autofix").

    Returns "ollama" so legacy callers (e.g. engine.py attribute logging)
    don't break. Smart routing happens in the gateway via task= parameter.
    """
    return "ollama"


def get_llm_for_bug(bug_type: str):
    """Get LLM gateway for the bug type.

    [ROOT-FIX 44-A] All bug types now use the same gateway — routing is
    handled via task="autofix" parameter on chat_sync(). The
    set_preferred_provider call is now a no-op (kept for backward compat).
    """
    try:
        from scp.llm_gateway import get_gateway
        gateway = get_gateway()
        preferred = select_provider_for_bug(bug_type)
        # [ROOT-FIX 44-A] No-op in new gateway — task= parameter handles routing.
        if hasattr(gateway, "set_preferred_provider"):
            gateway.set_preferred_provider(preferred)
        return gateway
    except Exception as e:
        logger.debug(f"[llm_fix] get_llm_for_bug({bug_type}) gateway unavailable: {e}")
        return None


def _call_smart_llm(prompt: str, bug_type: str, max_tokens: int = 4000) -> str | None:
    """Call LLM via gateway with task="autofix" (multi-model routing).

    [ROOT-FIX 44-A] Gateway routes task="autofix" → deepseek-r1:8b
    (user's strongest code-reasoning model). Cloud fallback to OpenRouter
    happens automatically if Ollama is down/unavailable.

    [OPT-17] bug_type is now used for STATS logging only (complexity tier),
    not for provider selection. deepseek-r1:8b handles all bug types well.

    Falls back to direct _call_openrouter() if gateway is unavailable
    (e.g. httpx not installed). This preserves backward compatibility —
    existing tests that mock _call_openrouter still work.
    """
    # [OPT-17] Stats-only classification — does not affect routing.
    if bug_type in _SIMPLE_BUG_TYPES:
        complexity_tier = "simple"
    elif bug_type in _COMPLEX_BUG_TYPES:
        complexity_tier = "complex"
    elif bug_type in _MEDIUM_BUG_TYPES:
        complexity_tier = "medium"
    else:
        complexity_tier = "unknown"
    logger.info(
        f"[llm_fix] [46] AutoFix routing: bug_type={bug_type} "
        f"complexity={complexity_tier} → task=autofix → OpenRouter V4 flash (primary) + Ollama R1:8b (fallback)"
    )

    system_prompt = (
        "You are a Python code fixer. Output ONLY a search-replace block "
        "in this exact format (no markdown fences, no explanation):\n\n"
        "<<<<<<< SEARCH\n"
        "<exact current code>\n"
        "=======\n"
        "<fixed code>\n"
        ">>>>>>> REPLACE\n\n"
        "Rules: (1) SEARCH must match the file exactly including indentation; "
        "(2) REPLACE must be valid Python; (3) if you cannot fix, output "
        "NO_FIX_POSSIBLE. Example:\n"
        "<<<<<<< SEARCH\n    except Exception as e:\n        pass\n"
        "=======\n    except Exception as e:\n        logger.warning(e)\n"
        ">>>>>>> REPLACE"
    )

    try:
        from scp.llm_gateway import chat_sync
        # [ROOT-FIX 44-A] task="autofix" → routes to deepseek-r1:8b via gateway.
        answer, provider_used = chat_sync(
            prompt, system_prompt=system_prompt, task="autofix"
        )
        if answer:
            logger.debug(
                f"[llm_fix] [44-A] Gateway returned via provider={provider_used} "
                f"(task=autofix, complexity={complexity_tier})"
            )
            return answer
        logger.debug(
            "[llm_fix] [44-A] Gateway returned empty answer, "
            "falling back to direct OpenRouter call"
        )
    except Exception as e:
        logger.debug(
            f"[llm_fix] [44-A] Gateway call failed ({e}), "
            f"falling back to direct OpenRouter call"
        )

    # Fallback: direct OpenRouter call (existing path, preserves backward compat)
    return _call_openrouter(prompt, max_tokens=max_tokens)


def _call_openrouter(prompt: str, max_tokens: int = 4000) -> str | None:
    """Call OpenRouter LLM with the given prompt. Returns LLM response text.

    [FIX #25] TẠI SAO: was max_tokens=1500 — too small for multi-line code patches.
    LLM truncated output mid-block → search-replace regex didn't match → patched=False.
    Reality > Model: tested Bug #2 (PredictiveEngine) → LLM generated correct fix
    but truncated at "logger.info(\"V" — incomplete. After fix: max_tokens=4000.
    """
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        # Try OPENROUTER_API_KEY_2, _3 (fallback)
        for i in (2, 3):
            api_key = os.environ.get(f"OPENROUTER_API_KEY_{i}", "")
            if api_key:
                break
    if not api_key:
        logger.warning("[llm_fix] No OPENROUTER_API_KEY set — cannot generate fix")
        return None

    base_url = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    model = os.environ.get("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct")

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a Python code fixer. Output ONLY a search-replace block in this exact format (no markdown fences, no explanation):\n\n<<<<<<< SEARCH\n<exact current code>\n=======\n<fixed code>\n>>>>>>> REPLACE\n\nRules: (1) SEARCH must match the file exactly including indentation; (2) REPLACE must be valid Python; (3) if you cannot fix, output NO_FIX_POSSIBLE."},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.1,  # low temp for deterministic fixes
    }

    try:
        req = urllib.request.Request(
            f"{base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://scp-vietnam.local",
                "X-Title": "SCP AutoFix",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:  # nosec B310 — OpenRouter API call to validated URL
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("choices", [{}])[0].get("message", {}).get("content", "")
    except urllib.error.HTTPError as e:
        logger.warning(f"[llm_fix] OpenRouter HTTP {e.code}: {e.read()[:200]}")
        return None
    except Exception as e:
        logger.warning(f"[llm_fix] OpenRouter call failed: {e}")
        return None


def _extract_search_replace_block(llm_response: str) -> str | None:
    """Extract the SEARCH/REPLACE (or legacy OLD/NEW) block from LLM response.

    [OPT-30] Updated to accept BOTH formats:
      - New canonical: <<<<<<< SEARCH / ======= / >>>>>>> REPLACE
      - Legacy:        <<<<<<< OLD    / ======= / >>>>>>> NEW  (backward compat)

    Both forms are normalized to the canonical `<<<<<<< SEARCH ... >>>>>>> REPLACE`
    output so downstream _apply_fix() only needs to handle one format.

    [FIX #25] TẠI SAO: LLMs sometimes forget the closing marker suffix
    (output `>>>>>>>` instead of `>>>>>>> REPLACE` or `>>>>>>> NEW`).
    Also sometimes use `======` (5 equals) instead of `=======` (7 equals).
    Reality > Model: tested Bug #3 → LLM output `>>>>>>>` (no NEW), regex
    didn't match → patched=False. After fix: regex accepts `>>>>>>>` with
    or without a trailing identifier suffix, and 5-7 equals.
    """
    if not llm_response:
        return None

    # Strategy 1: New canonical SEARCH/REPLACE format.
    # Accept lenient markers: optional suffix on `>>>>>>>` (e.g. `>>>>>>> REPLACE`
    # or bare `>>>>>>>`), 5-7 equals.
    sr_pattern = re.compile(
        r"<<<<<<<\s*SEARCH\s*\n(.*?)\n={5,7}\s*\n(.*?)\n>>>>>>>\s*(?:REPLACE)?\s*",
        re.DOTALL,
    )
    m = sr_pattern.search(llm_response)
    if m:
        return f"<<<<<<< SEARCH\n{m.group(1)}\n=======\n{m.group(2)}\n>>>>>>> REPLACE"

    # Strategy 2: Legacy OLD/NEW format (backward compat — in-flight LLM outputs
    # from before [OPT-30] still parse correctly).
    old_pattern = re.compile(
        r"<<<<<<<\s*OLD\s*\n(.*?)\n={5,7}\s*\n(.*?)\n>>>>>>>\s*(?:NEW)?\s*",
        re.DOTALL,
    )
    m = old_pattern.search(llm_response)
    if m:
        # Normalize to canonical SEARCH/REPLACE for downstream consumers.
        return f"<<<<<<< SEARCH\n{m.group(1)}\n=======\n{m.group(2)}\n>>>>>>> REPLACE"

    # Strategy 3: no explicit markers — look for ```python blocks as fallback
    # (handled by Strategy 2 in _apply_fix, so we return None here to signal
    # "no canonical block found, let _apply_fix try fenced code path").
    fenced = re.search(r"```(?:python)?\s*\n(.*?)\n```", llm_response, re.DOTALL)
    if fenced:
        return None  # fenced code is handled by Strategy 2 in _apply_fix
    return None


def generate_fix_for_bug(bug) -> str | None:
    """Generate a code fix for a BugReport using LLM.

    Args:
        bug: BugReport with file, line, bug_type, description, suggested_fix

    Returns:
        Search-replace block string (ready for AutoFix._apply_fix), or None.
    """
    if not _check_rate_limit():
        logger.warning("[llm_fix] Rate limit reached — skipping LLM fix generation")
        return None

    filepath = Path(bug.file)
    if not filepath.exists():
        logger.warning(f"[llm_fix] File not found: {filepath}")
        return None

    # [FULL-CONTEXT-FIX] Send ENTIRE file to LLM (not just 5 lines)
    # TẠI SAO: 5-line context = LLM mù → không thấy imports → fix fail
    # DeepSeek V4 context = 128K tokens → đủ cho file 15K tokens
    # Reality > Model: tested S311 (9 occurrences) → 5-line = 0% fix rate
    # → full-file = LLM thấy imports + all occurrences → fix đúng
    try:
        source = filepath.read_text(encoding="utf-8", errors="replace")
        lines = source.splitlines()
        bug_line_idx = max(0, bug.line - 1)  # 0-indexed

        # File too large? Send full file (up to 2000 lines ≈ 20K tokens)
        max_lines = int(os.environ.get("SCP_LLM_CONTEXT_LINES", "2000"))
        if len(lines) <= max_lines:
            # Full file — LLM sees everything
            context_lines = []
            for i, line in enumerate(lines):
                marker = " >>>" if i == bug_line_idx else "    "
                context_lines.append(f"# line {i+1}{marker}\n{line}")
            context = "\n".join(context_lines)
        else:
            # File too large — send 500 lines around bug (10x more than before)
            start = max(0, bug_line_idx - 250)
            end = min(len(lines), bug_line_idx + 250)
            context_lines = [f"# NOTE: Showing lines {start+1}-{end} of {len(lines)} (file truncated)"]
            for i in range(start, end):
                marker = " >>>" if i == bug_line_idx else "    "
                context_lines.append(f"# line {i+1}{marker}\n{lines[i]}")
            context = "\n".join(context_lines)
    except Exception as e:
        logger.warning(f"[llm_fix] Could not read {filepath}: {e}")
        return None

    # [OPT-30] Build improved prompt with few-shot example + explicit rules.
    # WHY: 7/15 LLM calls produced "No search-replace block found" error with
    # the old prose-only prompt. New prompt uses FIX_SYSTEM_PROMPT template
    # that includes a worked example + 5 explicit rules. _build_fix_prompt()
    # formats the template with bug-specific details.
    prompt = _build_fix_prompt(bug, context)

    # Call LLM
    # [OPT-17] Use smart provider routing — pick cheapest sufficient LLM
    # based on bug complexity. Falls back to direct OpenRouter call if
    # gateway is unavailable (backward-compatible).
    llm_response = _call_smart_llm(prompt, bug.bug_type, max_tokens=4000)
    if not llm_response:
        return None

    # Extract search-replace block
    fix_block = _extract_search_replace_block(llm_response)
    if fix_block:
        logger.info(f"[llm_fix] Generated fix for {filepath.name}:{bug.line}")
        return fix_block
    else:
        # Fallback: return raw LLM response (may contain fenced code)
        logger.info("[llm_fix] No search-replace block found, returning raw LLM response")
        return llm_response


def process_bug_with_llm(bug, autofix_engine) -> dict:
    """Process a bug: try pattern fix first, then LLM-generated fix.

    [V5.5-FIX] TẠI SAO: was always call LLM — 50 BareExceptPass bugs × 1 LLM call
    = 50 API calls, nhưng rate limit 20/giờ → 30 bugs "LLM fix generation failed"
    → STARTUP-GATE block. BareExceptPass là pattern CỨNG, fix trực tiếp không cần LLM.

    New flow:
      1. If bug has code patch already → use it
      2. If BareExceptPass → use _generate_bare_except_fix (NO LLM)
      3. Otherwise → call LLM (rate-limited)

    Args:
        bug: BugReport
        autofix_engine: AutoFixEngine instance

    Returns:
        Result dict from engine.process_bug(), with 'fix_source' flag.
    """
    # If bug already has a code patch (search-replace block), use normal flow.
    # [OPT-30] accept BOTH canonical SEARCH/REPLACE and legacy OLD/NEW markers.
    if bug.suggested_fix and (
        "<<<<<<< SEARCH" in bug.suggested_fix
        or "<<<<<<< OLD" in bug.suggested_fix
    ):
        result = autofix_engine.process_bug(bug)
        result["fix_source"] = "predefined"
        return result

    # [V5.5-FIX] Try pattern fixer first (no LLM needed for BareExceptPass)
    pattern_fix = _generate_bare_except_fix(bug)
    if pattern_fix:
        # Create BugReport with pattern-generated fix
        from scp.autofix.classifier import BugReport
        bug_with_fix = BugReport(
            file=bug.file,
            line=bug.line,
            bug_type=bug.bug_type,
            description=bug.description,
            suggested_fix=pattern_fix,
            tier=bug.tier,
            is_restraint=bug.is_restraint,
            is_reversible=bug.is_reversible,
            affects_logic=bug.affects_logic,
        )
        result = autofix_engine.process_bug(bug_with_fix)
        result["fix_source"] = "pattern_bare_except"
        result["llm_generated"] = False
        return result

    # [DNA7-FIX] Try deterministic fixers for simple bugs (no LLM, no evolution)
    det_fix = _generate_deterministic_fix(bug)
    if det_fix:
        logger.info(f"[llm_fix] Deterministic fix (no LLM): {bug.bug_type} at {bug.file}:{bug.line}")
        from scp.autofix.classifier import BugReport
        bug_with_fix = BugReport(
            file=bug.file, line=bug.line, bug_type=bug.bug_type,
            description=bug.description, suggested_fix=det_fix,
            tier=bug.tier, is_restraint=bug.is_restraint,
            is_reversible=bug.is_reversible, affects_logic=bug.affects_logic,
        )
        result = autofix_engine.process_bug(bug_with_fix)
        result["fix_source"] = "deterministic_no_llm"
        result["llm_generated"] = False
        return result

    # [V8.0-AUTOFIX] Try 5 new pattern fixers from EvolutionEngine
    # (opt-in via SCP_EVOLUTION_ENABLED=1)
    # TẠI SAO: bugs như type mismatch, null deref, race condition, SQL
    # injection, resource leak đều có pattern CỨNG → fix bằng regex + AST,
    # instant, $0. Chỉ fallback LLM nếu 5 pattern fixers không match.
    # New flow: predefined → pattern_bare_except →
    #           fix_type_mismatch → add_null_check → add_lock →
    #           parameterize_sql → add_context_manager → LLM
    if os.environ.get("SCP_EVOLUTION_ENABLED", "1") == "1":
        try:
            from scp.autofix.evolution import get_evolution_engine
            evo = get_evolution_engine()
            for fixer_name in (
                "fix_type_mismatch",
                "add_null_check",
                "add_lock",
                "parameterize_sql",
                "add_context_manager",
            ):
                fixer_fn = getattr(evo, fixer_name, None)
                if not fixer_fn:
                    continue
                try:
                    result = fixer_fn(bug)
                except Exception as e:
                    logger.debug(f"[llm_fix] {fixer_name} raised: {e}")
                    continue
                # If pattern fixer applied successfully, return result
                if result.get("action") == "fixed" and result.get("patched"):
                    result["fix_source"] = f"pattern_{fixer_name}"
                    result["llm_generated"] = False
                    return result
                # If WHY rejected, don't fall through to LLM (WHY is the chốt)
                if result.get("action") == "rejected_by_why":
                    result["fix_source"] = f"pattern_{fixer_name}"
                    result["llm_generated"] = False
                    return result
                # Otherwise (action="skipped") → try next pattern fixer
        except Exception as e:
            logger.debug(f"[llm_fix] evolution pattern fixers unavailable: {e}")

    # Generate fix via LLM (only for non-pattern bugs)
    llm_fix = generate_fix_for_bug(bug)
    if not llm_fix:
        return {
            "action": "skipped",
            "tier": int(getattr(bug, 'tier', 1)),
            "reason": "LLM fix generation failed",
            "llm_generated": False,
            "fix_source": "llm_failed",
        }

    # Create new BugReport with LLM-generated fix
    from scp.autofix.classifier import BugReport
    bug_with_fix = BugReport(
        file=bug.file,
        line=bug.line,
        bug_type=bug.bug_type,
        description=bug.description,
        suggested_fix=llm_fix,
        tier=bug.tier,
        is_restraint=bug.is_restraint,
        is_reversible=bug.is_reversible,
        affects_logic=bug.affects_logic,
    )

    # Process with AutoFix (will call _apply_fix with the LLM-generated patch)
    result = autofix_engine.process_bug(bug_with_fix)
    result["llm_generated"] = True
    result["fix_source"] = "llm"
    return result


# ============================================================
# [DNA7-FIX] DETERMINISTIC FIXERS — không cần LLM cho lỗi đơn giản
# ============================================================

def _generate_deterministic_fix(bug) -> str | None:
    """Generate fix cho lỗi đơn giản KHÔNG cần LLM.

    DNA #7: AutoFix safe — có cấp bậc.
    Lỗi đơn giản = Tier 1 (auto-fix no log) → deterministic, không LLM.
    Lỗi phức tạp = Tier 2+ → gọi LLM.

    Returns search-replace block, or None if not a simple bug.
    """
    filepath = Path(bug.file)
    if not filepath.exists():
        return None

    try:
        source = filepath.read_text(encoding="utf-8")
        lines = source.splitlines()
        bug_line_idx = bug.line - 1
        if bug_line_idx >= len(lines):
            return None

        bug_line = lines[bug_line_idx]
        bug_type = bug.bug_type

        # --- S101: assert → if/raise ---
        if bug_type == "RuffSecurity_S101" or bug_type == "S101":
            import re
            # Pattern: assert condition
            m = re.match(r'^(\s*)assert\s+(.+)$', bug_line)
            if m:
                indent = m.group(1)
                condition = m.group(2).strip()
                old = bug_line.rstrip()
                new = f"{indent}if not ({condition}):\n{indent}    raise AssertionError(f'Assertion failed: {condition}')"
                return f"<<<<<<< SEARCH\n{old}\n=======\n{new}\n>>>>>>> REPLACE"

        # --- S110: except: continue → except: logger.warning + continue ---
        if bug_type in ("RuffSecurity_S110", "S110", "RuffSecurity_S112", "S112"):
            import re
            # Check if next line is `continue` or `pass`
            next_line = lines[bug_line_idx + 1].strip() if bug_line_idx + 1 < len(lines) else ""
            if next_line in ("continue", "pass"):
                m = re.match(r'^(\s*)(except\s+.*)$', bug_line)
                if m:
                    indent = m.group(1)
                    except_clause = m.group(2).rstrip().rstrip(":")
                    old = f"{bug_line.rstrip()}\n{indent}    {next_line}"
                    new = f"{except_clause} as _e:\n{indent}    logger.warning(f'Silent except: {{_e!r}}')\n{indent}    {next_line}"
                    return f"<<<<<<< SEARCH\n{old}\n=======\n{new}\n>>>>>>> REPLACE"

        # --- F401: unused import → xóa dòng ---
        if bug_type in ("F401", "UnusedImport"):
            old = bug_line.rstrip()
            # Just remove the line (replace with empty)
            return f"<<<<<<< SEARCH\n{old}\n=======\n>>>>>>> REPLACE"

        # --- F841: unused variable → thêm _ prefix ---
        if bug_type in ("F841", "UnusedVariable"):
            import re
            m = re.match(r'^(\s*)(\w+)\s*=', bug_line)
            if m:
                indent = m.group(1)
                var_name = m.group(2)
                old = bug_line.rstrip()
                new = bug_line.rstrip().replace(var_name, f"_{var_name}", 1)
                return f"<<<<<<< SEARCH\n{old}\n=======\n{new}\n>>>>>>> REPLACE"

        # --- E702: x; y → tách dòng ---
        if bug_type in ("E702",):
            import re
            m = re.match(r'^(\s*)(.+);(.+)$', bug_line)
            if m:
                indent = m.group(1)
                first = m.group(2).strip()
                second = m.group(3).strip()
                old = bug_line.rstrip()
                new = f"{indent}{first}\n{indent}{second}"
                return f"<<<<<<< SEARCH\n{old}\n=======\n{new}\n>>>>>>> REPLACE"

        # --- B007: unused loop var → _ prefix ---
        if bug_type in ("B007",):
            import re
            m = re.match(r'^(\s*)for\s+(\w+)\s+in\s+', bug_line)
            if m:
                var_name = m.group(2)
                old = bug_line.rstrip()
                new = bug_line.rstrip().replace(f" {var_name} ", f" _{var_name} ", 1)
                return f"<<<<<<< SEARCH\n{old}\n=======\n{new}\n>>>>>>> REPLACE"

        # Not a simple bug → return None (caller will call LLM)
        return None

    except Exception:
        return None
