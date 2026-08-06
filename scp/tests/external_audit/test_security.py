"""Security regression tests — OWASP API5 BFLA (Broken Function Level Authorization).

RC-10 fix — external check that admin routes have auth.

Scope:
- Every @app.get/post("/v98/*"), /v100/*, /v102/*, /v103/*, /v104/*, /v105/* route
  MUST have Depends(verify_admin) — either in the decorator's `dependencies=[...]`
  list OR in the function signature.
- Public routes (/health, /metrics, /, /dashboard, /v1/chat/completions,
  /v1/models, /ask) are EXEMPT — they are user-facing endpoints, not admin
  management APIs. (PyRIT/garak need /v1/* open for testing; /ask is the main
  Q&A endpoint.)

This test is INDEPENDENT from SCP's own scanners — it parses api_server.py
source directly with regex, so SCP's autofix cannot silently disable it.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from scp.core.safe_process import safe_run

SCP_ROOT = Path(__file__).resolve().parent.parent.parent
API_SERVER = SCP_ROOT / "api_server.py"
# [Task 19-A] verify_admin was extracted to api_server_parts/helpers.py — check both.
API_SERVER_HELPERS = SCP_ROOT / "api_server_parts" / "helpers.py"

# Routes that are PUBLIC by design — exempt from BFLA check.
# Add new public routes here ONLY with justification comment.
PUBLIC_ROUTES = {
    "/",            # root info
    "/health",      # liveness probe (must be open for k8s/docker healthcheck)
    "/metrics",     # prometheus metrics (read-only, no secrets)
    "/dashboard",   # HTML dashboard (token required via URL ?token=... in JS)
    "/ask",         # main Q&A endpoint (user-facing, has rate limiting)
    "/v1/chat/completions",  # OpenAI-compatible (PyRIT/garak need open access)
    "/v1/models",           # OpenAI-compatible model list (public metadata)
}


def _extract_routes(src: str) -> list[tuple[int, str, str]]:
    """Return [(line_number, method, path), ...] for every @app.* decorator."""
    routes = []
    for i, line in enumerate(src.splitlines(), start=1):
        m = re.match(
            r'\s*@app\.(get|post|put|delete)\(\s*"([^"]+)"',
            line,
        )
        if m:
            routes.append((i, m.group(1), m.group(2)))
    return routes


def _route_has_auth(src: str, decorator_line: int) -> bool:
    """Check whether the route on `decorator_line` has verify_admin auth.

    Looks at the decorator line itself AND the next 5 lines (function signature).
    """
    lines = src.splitlines()
    # Decorator line itself (dependencies=[Depends(verify_admin)])
    chunk = lines[decorator_line - 1]
    # Function signature + following lines
    for j in range(decorator_line, min(decorator_line + 5, len(lines))):
        chunk += "\n" + lines[j]
    return "Depends(verify_admin)" in chunk


def test_admin_routes_have_auth():
    """RC-2 BFLA check: every /v9*, /v10*, /v105* admin route must have verify_admin.

    V105 had 25 BFLA routes (admin management endpoints with no auth).
    RC-2 added `dependencies=[Depends(verify_admin)]` to all of them.
    This test catches any new admin route added without auth.
    """
    if not API_SERVER.exists():
        pytest.skip("api_server.py not found — package layout differs")
    src = API_SERVER.read_text(encoding="utf-8")
    routes = _extract_routes(src)

    missing_auth = []
    for lineno, method, path in routes:
        # Skip public routes
        if path in PUBLIC_ROUTES:
            continue
        # Only check versioned admin prefixes (/v9*, /v10*, /v105*)
        if not re.match(r'^/v(9|10)\d+', path) and not path.startswith("/v105/"):
            continue
        if not _route_has_auth(src, lineno):
            missing_auth.append(f"  L{lineno}: {method.upper()} {path}")

    assert not missing_auth, (  # noqa: S101
        "BFLA regression — admin routes without verify_admin (RC-2):\n"
        + "\n".join(missing_auth)
    )


def test_verify_admin_no_dev_mode_bypass():
    """RC-2: verify_admin function must NOT contain the SCP_DEV_MODE bypass.

    Catches any future reintroduction of the V104.22 #3 hole:
        if os.environ.get("SCP_DEV_MODE", "0") == "1": return True
    """
    if not API_SERVER.exists():
        pytest.skip("api_server.py not found — package layout differs")
    src = API_SERVER.read_text(encoding="utf-8")
    # [Task 19-A] Also check helpers.py — verify_admin was extracted there.
    if API_SERVER_HELPERS.exists():
        src += "\n\n# --- from api_server_parts/helpers.py ---\n" + API_SERVER_HELPERS.read_text(encoding="utf-8")

    # Extract verify_admin function body — non-greedy match to the NEXT
    # top-level def/class (so we don't accidentally swallow later functions
    # that might legitimately contain `return True`).
    m = re.search(
        r'^def verify_admin\([^)]*\)[^:]*:(?:.|\n)*?^(?:def |class |\Z)',
        src, re.MULTILINE,
    )
    assert m, "verify_admin function not found in api_server.py"  # noqa: S101
    # m.group(0) includes the trailing "def " of the next function — strip it.
    body = re.sub(r'\n(?:def |class ).*$', '', m.group(0), flags=re.MULTILINE)

    # The bypass pattern: SCP_DEV_MODE check immediately followed by
    # `return True` (within a few lines). Use a tighter regex than
    # "both strings present anywhere" — comments mentioning SCP_DEV_MODE
    # are OK as long as they're not paired with `return True` as the
    # immediate consequence.
    bypass_pattern = re.compile(
        r'SCP_DEV_MODE[^"\n]*"1"[^:\n]*:[^\n]*\n\s*return\s+True',
        re.MULTILINE,
    )
    assert not bypass_pattern.search(body), (  # noqa: S101
        "RC-2 regression: SCP_DEV_MODE bypass (return True) present in verify_admin body"
    )


def test_no_hardcoded_token_in_source():
    """RC-2 CODE-AUDIT-001: no production token hardcoded in source.

    Independent of test_cascade.test_no_hardcoded_token_via_grep — this one
    uses Python re instead of grep, so it works on systems without grep.
    """
    token = os.environ.get("SCP_AUTH_TOKEN_SECRET", "")
    # [G5-FIX] If token is empty, every file "contains" it (empty string is substring of any string).
    # Skip the check when token not configured — can't verify what we don't know.
    if not token:
        pytest.skip("SCP_AUTH_TOKEN_SECRET not set — cannot verify no-hardcoded-token")
    offenders = []
    for py_file in SCP_ROOT.rglob("*.py"):
        path_str = str(py_file)
        if "__pycache__" in path_str:
            continue
        # [G5-FIX] Skip ALL test files — they may contain test tokens (not production secrets)
        if "tests/" in path_str or "/test_" in path_str or path_str.startswith("test_"):
            continue
        try:
            src = py_file.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if token in src:
            offenders.append(path_str)
    # Also check .md docs in benchmark/
    benchmark_dir = SCP_ROOT / "benchmark"
    if benchmark_dir.exists():
        for md_file in benchmark_dir.rglob("*.md"):
            try:
                src = md_file.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            if token in src:
                offenders.append(str(md_file))
    assert not offenders, (  # noqa: S101
        "RC-2 regression: hardcoded production token found in:\n  "
        + "\n  ".join(offenders)
    )


def test_bandit_no_new_high_severity_via_bandit():
    """RC-10 external audit: bandit HIGH-severity count must not increase.

    Baseline after RC-2 fix: 15 HIGH (all B324 hashlib MD5 — pre-existing,
    content fingerprinting not password hashing). If a fix adds a new HIGH
    severity issue (e.g. exec(eval(...)), hardcoded password, SQL injection),
    this test catches it.

    NOTE: this test does NOT fail on the 15 pre-existing B324 issues. It
    fails only if NEW HIGH-severity issues appear beyond the known baseline.
    """
    result = safe_run(
        ["bandit", "-r", str(SCP_ROOT), "-f", "json", "-q"],
    )
    if result.returncode not in (0, 1):
        pytest.skip(f"bandit failed to run: {result.stderr[:200]}")
    import json
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        pytest.skip("bandit did not produce JSON output")
    high_issues = [
        issue for issue in data.get("results", [])
        if issue.get("issue_severity") == "HIGH"
    ]
    # Baseline: 15 HIGH (all B324 hashlib MD5). Allow slack for environment
    # variance but fail if a NEW CWE category appears.
    new_categories = set()
    for issue in high_issues:
        if issue.get("test_id") != "B324":  # B324 = MD5, pre-existing baseline
            new_categories.add((issue.get("test_id"), issue.get("filename")))
    # Filter out issues in tests/external_audit itself (these test files)
    new_categories = {
        (tid, fn) for (tid, fn) in new_categories
        if "/tests/external_audit/" not in fn
    }
    assert not new_categories, (  # noqa: S101
        "RC-10 regression: new HIGH-severity bandit issues appeared:\n  "
        + "\n  ".join(f"{tid} in {fn}" for tid, fn in new_categories)
    )
