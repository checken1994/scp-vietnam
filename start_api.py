"""
SCP V99 — API Server Startup Script
=====================================

Usage:
    python start_api.py                          # default port 8000
    python start_api.py --port 8080              # custom port
    python start_api.py --host 0.0.0.0           # bind all interfaces
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# [V5.5-FIX] Auto-load .env file — TẠI SAO: Gà chạy `python start_api.py`
# nhưng .env (chứa OPENROUTER_API_KEY, GROQ_API_KEY...) không được load
# → llm_fix.py báo "No OPENROUTER_API_KEY set" → AutoFix không fix được
# → STARTUP-GATE block server. python-dotenv load .env vào os.environ.
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    # dotenv not installed — try manual load
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in __import__("os").environ:
                __import__("os").environ[key] = value


def main():
    parser = argparse.ArgumentParser(description="Start SCP V99 API Server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true", help="Auto-reload (dev)")
    args = parser.parse_args()

    print(f"""
╔══════════════════════════════════════════════════╗
║  SCP V99 — Self-Correcting Pipeline API          ║
║  53 SLMs + V98 Security + FalsificationEngine    ║
╠══════════════════════════════════════════════════╣
║  Host:   {args.host:<40s}║
║  Port:   {args.port:<40d}║
║  Dashboard: http://{args.host}:{args.port}/dashboard{' ' * (40 - len(f'http://{args.host}:{args.port}/dashboard'))}║
║  Docs:     http://{args.host}:{args.port}/docs{' ' * (40 - len(f'http://{args.host}:{args.port}/docs'))}║
╚══════════════════════════════════════════════════╝
""")

    import uvicorn
    uvicorn.run(
        "scp.api_server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
