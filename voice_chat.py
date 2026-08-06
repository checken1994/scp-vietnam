"""
SCP Voice Chat — Chạy trực tiếp trên PC, không cần browser.

Cách dùng:
  python voice_chat.py

Yêu cầu:
  pip install openai-whisper gTTS playsound sounddevice numpy

Luồng:
  1. Load Whisper (STT)
  2. Mở microphone → listen liên tục
  3. Phát hiện speech → transcribe → SCP judge → TTS → play loa
  4. Loop 24/7
"""
from __future__ import annotations

import io
import logging
import os
import sys
import tempfile
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
logger = logging.getLogger("scp.voice")

# ============================================================
# 1. STT — Speech to Text (Whisper)
# ============================================================
_whisper_model = None

def load_whisper():
    global _whisper_model
    try:
        import whisper
        model_size = os.environ.get("SCP_WHISPER_MODEL", "base")
        logger.info(f"Đang load Whisper ({model_size})... 5-10s")
        _whisper_model = whisper.load_model(model_size)
        logger.info("✓ Whisper sẵn sàng")
    except ImportError:
        logger.error("❌ Chưa cài Whisper. Chạy: pip install openai-whisper")
        sys.exit(1)

def stt(audio_bytes: bytes) -> str:
    """Audio bytes → text."""
    if not _whisper_model:
        return ""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(audio_bytes)
        path = f.name
    try:
        result = _whisper_model.transcribe(path, language="vi")
        return result.get("text", "").strip()
    except Exception as e:
        logger.error(f"STT error: {e}")
        return ""
    finally:
        try: os.unlink(path)
        except Exception: pass

# ============================================================
# 2. TTS — Text to Speech (gTTS)
# ============================================================
def tts(text: str) -> str:
    """Text → MP3 file path."""
    if not text:
        return ""
    try:
        from gtts import gTTS
        tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        gTTS(text, lang="vi", slow=False).save(tmp.name)
        return tmp.name
    except ImportError:
        logger.error("❌ Chưa cài gTTS. Chạy: pip install gTTS")
        return ""
    except Exception as e:
        logger.error(f"TTS error: {e}")
        return ""

def play_audio(path: str):
    """Play MP3 file qua loa."""
    if not path or not os.path.exists(path):
        return
    try:
        from playsound import playsound
        playsound(path)
    except ImportError:
        # Fallback: dùng os.startfile (Windows) hoặc afplay (Mac)
        import platform
        if platform.system() == "Windows":
<<<<<<< HEAD
            os.startfile(path)  # type: ignore  # nosec B606 - Windows-native file open (no shell=True, safe by design)
            time.sleep(2)  # Đợi play
        elif platform.system() == "Darwin":
            # [SECURITY-FIX] B605: os.system with f-string = shell injection if path contains '
            # Use subprocess.run with shell=False (list form) — no shell parsing
            import subprocess as _sp
            _sp.run(["afplay", path], check=False, timeout=30)  # noqa: S603,S607 — afplay is a hardcoded macOS tool, path is a temp file we control
=======
            os.startfile(path)  # type: ignore
            time.sleep(2)  # Đợi play
        elif platform.system() == "Darwin":
            os.system(f"afplay '{path}'")
>>>>>>> 3c1d558671d09b1eb1de62d9b3a373c36929bd5f
    except Exception as e:
        logger.error(f"Play error: {e}")
    finally:
        try: os.unlink(path)
        except Exception: pass

# ============================================================
# 3. SCP Judge
# ============================================================
_judge = None

def get_judge():
    global _judge
    if _judge is None:
        os.environ.setdefault("SCP_AUTH_TOKEN_SECRET", "voice-chat")
        os.environ.setdefault("SCP_SKIP_STARTUP_GATE", "1")
        from scp.runtime.judge import RealityJudge
        _judge = RealityJudge()
        logger.info("✓ RealityJudge sẵn sàng")
    return _judge

def judge(text: str) -> dict:
    try:
        v = get_judge().judge(text, "", cycle_count=0)
        return {
            "verdict": v.verdict,
            "answer": v.final_answer or "",
            "confidence": v.confidence,
            "domain": v.domain or "",
            "reasoning": v.reasoning or "",
        }
    except Exception as e:
        return {"verdict": "UNKNOWN", "answer": "", "confidence": 0, "domain": "", "reasoning": str(e)}

# ============================================================
# 4. Microphone — Record audio
# ============================================================
def record_audio(duration: float = 5.0, sample_rate: int = 16000) -> bytes:
    """Record từ mic → WAV bytes."""
    try:
        import wave

        import numpy as np
        import sounddevice as sd

        logger.info(f"🎤 Đang ghi ({duration}s)...")
        audio = sd.rec(int(duration * sample_rate), samplerate=sample_rate, channels=1, dtype="int16")
        sd.wait()

        # Check if silence (all zeros)
        if np.max(np.abs(audio)) < 100:
            return b""  # Silent

        # Convert to WAV bytes
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(audio.tobytes())
        return buf.getvalue()
    except ImportError:
        logger.error("❌ Chưa cài sounddevice. Chạy: pip install sounddevice numpy")
        return b""
    except Exception as e:
        logger.error(f"Record error: {e}")
        return b""

# ============================================================
# 5. Main loop
# ============================================================
def main():
    print()
    print("╔══════════════════════════════════════════════════╗")
    print("║  SCP Voice Chat — Trò chuyện trực tiếp          ║")
    print("║                                                  ║")
    print("║  Nói vào mic → SCP nghe → SCP trả lời            ║")
    print("║  Ctrl+C để thoát                                 ║")
    print("╚══════════════════════════════════════════════════╝")
    print()

    # Load components
    load_whisper()
    get_judge()

    # Greeting
    print("\n🤖 SCP: Xin chào! Tôi đã sẵn sàng. Hãy nói gì đó.")
    play_audio(tts("Xin chào! Tôi đã sẵn sàng. Hãy nói gì đó."))

    print("\n" + "=" * 50)
    print("  🎤 MIC ĐANG BẬT — nói đi (5s mỗi lượt)")
    print("=" * 50 + "\n")

    try:
        while True:
            # 1. Record 5s
            audio = record_audio(duration=5.0)
            if not audio:
                continue  # Silent, skip

            # 2. STT
            text = stt(audio)
            if not text:
                print("  (không nghe rõ)")
                continue

            print(f"\n  📝 Bạn: {text}")

            # 3. Judge
            result = judge(text)
            verdict = result["verdict"]
            answer = result["answer"]
            conf = result["confidence"]

            print(f"  ⚖️ Verdict: {verdict} (conf={conf*100:.0f}%)")
            if answer:
                print(f"  💬 Answer: {answer}")

            # 4. TTS + Play
            response = answer or result["reasoning"] or "Tôi không chắc."
            print(f"  🔊 SCP nói: {response[:100]}")
            play_audio(tts(response))

            print("\n" + "-" * 50 + "\n")

    except KeyboardInterrupt:
        print("\n\n👋 Tạm biệt!")
        play_audio(tts("Tạm biệt!"))

if __name__ == "__main__":
    main()


# ============================================================
# SCP AGENT — Truy cập PC + tự động hóa tác vụ
# ============================================================

def web_search_direct(query: str, max_results: int = 5) -> list[dict]:
    """Tìm kiếm web trực tiếp — KHÔNG qua API, scrape thật.

    Dùng requests + BeautifulSoup → lấy kết quả từ DuckDuckGo (no API key).
    """
    try:
        from urllib.parse import quote_plus

        import requests

        url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        resp = requests.get(url, headers=headers, timeout=15)

        if resp.status_code != 200:
            return []

        # Parse HTML (không cần BeautifulSoup — dùng regex cho nhẹ)
        import re
        results = []
        # DuckDuckGo HTML results
        blocks = re.findall(r'<a[^>]*class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>.*?<a[^>]*class="result__snippet"[^>]*>(.*?)</a>', resp.text, re.DOTALL)
        for url_raw, title_raw, snippet_raw in blocks[:max_results]:
            title = re.sub(r'<[^>]+>', '', title_raw).strip()
            snippet = re.sub(r'<[^>]+>', '', snippet_raw).strip()
            # DuckDuckGo redirect URLs
            if "uddg=" in url_raw:
                from urllib.parse import parse_qs, urlparse
                parsed = urlparse(url_raw)
                params = parse_qs(parsed.query)
                url_clean = params.get("uddg", [url_raw])[0]
            else:
                url_clean = url_raw
            results.append({"title": title, "url": url_clean, "snippet": snippet})

        return results
    except Exception as e:
        logger.error(f"Web search error: {e}")
        return []


def web_read_page(url: str) -> str:
    """Đọc nội dung trang web trực tiếp — scrape text thật.

    Dùng requests → lấy HTML → strip tags → trả text.
    """
    try:
        import re

        import requests

        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        resp = requests.get(url, headers=headers, timeout=15)

        if resp.status_code != 200:
            return f"HTTP {resp.status_code}"

        html = resp.text
        # Remove scripts + styles
        html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL)
        html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL)
        # Strip tags
        text = re.sub(r'<[^>]+>', ' ', html)
        # Clean whitespace
        text = re.sub(r'\s+', ' ', text).strip()
        return text[:5000]  # Limit 5000 chars
    except Exception as e:
        return f"Error: {e}"


def open_file(path: str) -> str:
    """Mở file trên PC → đọc nội dung."""
    try:
        from pathlib import Path
        p = Path(path).expanduser()
        if not p.exists():
            return f"File không tồn tại: {path}"
        if p.is_dir():
            files = [str(f.name) for f in p.iterdir()][:20]
            return f"Thư mục {path} có {len(list(p.iterdir()))} files:\n" + "\n".join(files)
        return p.read_text(encoding="utf-8", errors="ignore")[:5000]
    except Exception as e:
        return f"Error: {e}"


def run_command(cmd: str) -> str:
<<<<<<< HEAD
    """Chạy lệnh CMD/PowerShell → trả output.

    [SECURITY-FIX] B602: was shell=True → command injection vulnerability.
    Now uses shlex.split() + shell=False. Only allows whitelisted commands.
    """
    try:
        import subprocess
        import shlex
        # [SECURITY-FIX] B602: shell=False + shlex.split = no shell injection
        # Whitelist of allowed command prefixes (defense in depth)
        ALLOWED = {"python", "pip", "git", "ruff", "pytest", "bandit", "vulture", "echo", "dir", "ls", "cat"}
        parts = shlex.split(cmd)
        if not parts:
            return "(empty command)"
        if parts[0] not in ALLOWED:
            return f"(blocked: '{parts[0]}' not in whitelist {sorted(ALLOWED)})"
        result = subprocess.run(  # noqa: S603 — shell=False, command whitelisted
            parts, shell=False, capture_output=True, text=True, timeout=30
=======
    """Chạy lệnh CMD/PowerShell → trả output."""
    try:
        import subprocess
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=30
>>>>>>> 3c1d558671d09b1eb1de62d9b3a373c36929bd5f
        )
        output = result.stdout.strip()
        if result.stderr:
            output += f"\n[STDERR] {result.stderr.strip()}"
        return output[:5000] if output else "(no output)"
    except Exception as e:
        return f"Error: {e}"


def open_browser(url: str) -> str:
    """Mở trang web trong browser mặc định."""
    try:
        import webbrowser
        webbrowser.open(url)
        return f"✓ Đã mở: {url}"
    except Exception as e:
        return f"Error: {e}"


def process_command(text: str) -> str:
    """Xử lý lệnh từ user — quyết định judge hay thực hiện tác vụ."""
    text_lower = text.lower().strip()

    # 1. Web search
    if any(kw in text_lower for kw in ["tìm kiếm", "tìm", "search", "google", "tra cứu"]):
        # Extract query (remove command words)
        query = text
        for kw in ["tìm kiếm", "tìm", "search", "google", "tra cứu", "cho tôi", "giúp tôi"]:
            query = query.replace(kw, "").strip()
        if not query:
            return "Bạn muốn tìm gì?"

        results = web_search_direct(query)
        if not results:
            return f"Không tìm thấy kết quả cho: {query}"

        response = f"Tìm thấy {len(results)} kết quả cho '{query}':\n"
        for i, r in enumerate(results[:3]):
            response += f"\n{i+1}. {r['title']}\n   {r['snippet'][:100]}\n"

        # Đọc trang đầu tiên để lấy chi tiết
        if results:
            detail = web_read_page(results[0]["url"])
            if detail and not detail.startswith("HTTP"):
                response += f"\n📄 Nội dung: {detail[:500]}..."

        return response

    # 2. Open file
    if any(kw in text_lower for kw in ["mở file", "đọc file", "open file", "xem file"]):
        # Extract path
        import re
        path_match = re.search(r'(?:file|file\s+)(.+)', text, re.IGNORECASE)
        if path_match:
            path = path_match.group(1).strip()
            return open_file(path)
        return "Đường dẫn file nào?"

    # 3. Run command
    if any(kw in text_lower for kw in ["chạy lệnh", "run command", "cmd", "terminal"]):
        import re
        cmd_match = re.search(r'(?:lệnh|command|cmd)\s*[:\s]*(.+)', text, re.IGNORECASE)
        if cmd_match:
            cmd = cmd_match.group(1).strip()
            return run_command(cmd)
        return "Lệnh gì?"

    # 4. Open browser
    if any(kw in text_lower for kw in ["mở trang", "open url", "mở web", "vào web"]):
        import re
        url_match = re.search(r'https?://\S+', text)
        if url_match:
            return open_browser(url_match.group(0))
        return "URL nào?"

    # 5. Default: SCP judge (verify claims)
    result = judge(text)
    verdict = result["verdict"]
    answer = result["answer"]
    conf = result["confidence"]

    if answer:
        return f"[{verdict} {conf*100:.0f}%] {answer}"
    elif result["reasoning"]:
        return f"[{verdict}] {result['reasoning'][:200]}"
    return f"[{verdict}] Tôi không có đủ bằng chứng."


# ============================================================
# MAIN LOOP (updated with agent capabilities)
# ============================================================

def main_agent():
    """Main loop với agent capabilities."""
    print()
    print("╔══════════════════════════════════════════════════════╗")
    print("║  SCP Agent — Voice Chat + Tự động hóa PC            ║")
    print("║                                                      ║")
    print("║  Lệnh voice:                                         ║")
    print("║  • 'Tìm kiếm <query>'  → search web trực tiếp        ║")
    print("║  • 'Mở file <path>'    → đọc file trên PC            ║")
    print("║  • 'Chạy lệnh <cmd>'   → chạy CMD/PowerShell         ║")
    print("║  • 'Mở web <url>'      → mở browser                  ║")
    print("║  • Câu hỏi bất kỳ      → SCP judge (verify)          ║")
    print("║                                                      ║")
    print("║  Ctrl+C để thoát                                     ║")
    print("╚══════════════════════════════════════════════════════╝")
    print()

    # Load components
    load_whisper()
    get_judge()

    # Greeting
    greeting = "Xin chào Anh Minh! Tôi là SCP Agent. Tôi có thể tìm web, đọc file, chạy lệnh, hoặc kiểm chứng thông tin. Hãy nói gì đó."
    print(f"\n🤖 SCP: {greeting}")
    play_audio(tts(greeting))

    print("\n" + "=" * 55)
    print("  🎤 MIC ĐANG BẬT — nói đi (5s mỗi lượt)")
    print("=" * 55 + "\n")

    try:
        while True:
            # 1. Record 5s
            audio = record_audio(duration=5.0)
            if not audio:
                continue

            # 2. STT
            text = stt(audio)
            if not text:
                print("  (không nghe rõ)")
                continue

            print(f"\n  📝 Bạn: {text}")

            # 3. Process (agent or judge)
            response = process_command(text)

            print(f"  🤖 SCP: {response[:300]}")

            # 4. TTS + Play
            play_audio(tts(response[:500]))  # Limit TTS to 500 chars

            print("\n" + "-" * 55 + "\n")

    except KeyboardInterrupt:
        print("\n\n👋 Tạm biệt!")
        play_audio(tts("Tạm biệt!"))


if __name__ == "__main__":
    # Chạy agent mode (có tác vụ PC) thay vì chat-only
    main_agent()
