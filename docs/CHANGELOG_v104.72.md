# SCP V104.72 — Plugin & Integration Release

## Summary
- Đơn giản hóa API: `scp_simple.py` (Verifier class, 1 dòng)
- 3 integrations: LangChain, LlamaIndex, MCP
- README 5-minute với 3 ví dụ
- pyproject.toml cho PyPI packaging
- 363 tests pass, 0 failed

## New Files
- `scp_simple.py` — Simple API (Verifier class, verify/ask/check)
- `integrations/__init__.py` — Integrations package
- `integrations/langchain_scp.py` — LangChain SCPVerifier + SCPTool
- `integrations/llamaindex_scp.py` — LlamaIndex SCPNodePostprocessor + SCPQueryEngine
- `integrations/mcp_scp.py` — MCP server (3 tools: verify, ask, fact_check)
- `README.md` — 5-minute quickstart với 3 ví dụ
- `pyproject.toml` — PyPI packaging config

## Developer Experience (DX)
- **Before**: 59 endpoints, 1.7s init, 82MB RAM, cần server
- **After**: 1 method (`verify()`), lazy loading, BYO-Key, sync/async

## Usage
```python
from scp_simple import Verifier

v = Verifier()
r = v.verify("Thủ đô Pháp?", "Paris")
print(r.verdict, r.confidence, r.sources)
```

## Integrations
- LangChain: `llm | SCPVerifier()` hoặc `SCPTool()`
- LlamaIndex: `SCPNodePostprocessor()` filter false nodes
- MCP: Claude Desktop / Cursor / Windsurf — 3 tools (verify, ask, fact_check)

## Verification
- 6 static tools: 0 real errors
- 363 tests pass, 0 failed
- All integrations compile + tested
