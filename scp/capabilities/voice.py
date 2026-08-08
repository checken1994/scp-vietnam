"""
[Capability 4] Voice I/O — STT (Speech-to-Text) + TTS (Text-to-Speech).

TÁI SAO: SCP chỉ nhận text. User muốn hỏi bằng giọng nói → cần Whisper STT.
SCP trả lời text → cần TTS để user nghe được.

STT: openai-whisper (local, free)
TTS: edge-tts (Microsoft Edge TTS, free)
"""
from __future__ import annotations
import logging, tempfile, os
<<<<<<< HEAD
=======
from typing import Optional
>>>>>>> 847232d (Initial commit - SCP Vietnam V99)

logger = logging.getLogger("scp.capabilities.voice")

class VoiceHandler:
    """STT + TTS handler."""
    
    def __init__(self):
        self._whisper_model = None
    
<<<<<<< HEAD
    def transcribe(self, audio_bytes: bytes, language: str = None) -> str:
=======
    def transcribe(self, audio_bytes: bytes, language: Optional[str] = None) -> str:
>>>>>>> 847232d (Initial commit - SCP Vietnam V99)
        """STT: audio bytes → text (Whisper local)."""
        try:
            import whisper
            if self._whisper_model is None:
                self._whisper_model = whisper.load_model("base")
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                f.write(audio_bytes)
                f.flush()
                result = self._whisper_model.transcribe(f.name, language=language)
                os.unlink(f.name)
                return result.get("text", "")
        except ImportError:
            logger.warning("whisper not installed — pip install openai-whisper")
            return ""
        except Exception as e:
            logger.debug(f"STT error: {e}")
            return ""
    
    async def speak(self, text: str, voice: str = "vi-VN-HoaiMyNeural") -> bytes:
        """TTS: text → audio bytes (edge-tts, free)."""
        try:
            import edge_tts
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                communicate = edge_tts.Communicate(text, voice)
                await communicate.save(f.name)
                with open(f.name, "rb") as audio:
                    data = audio.read()
                os.unlink(f.name)
                return data
        except ImportError:
            logger.warning("edge-tts not installed — pip install edge-tts")
            return b""
        except Exception as e:
            logger.debug(f"TTS error: {e}")
            return b""
