"""
[Capability 5] Image Understanding (VLM) — Vision Language Model.

TÁI SAO: SCP chỉ detect jailbreak trong ảnh (OCR). Không hiểu nội dung ảnh.
VLM cho phép user gửi ảnh + hỏi "What's in this image?" → SCP verify.

Uses: LLaVA (local via Ollama) hoặc OpenRouter vision model.
"""
from __future__ import annotations
import logging, base64

logger = logging.getLogger("scp.capabilities.vision")

class VisionHandler:
    """Image understanding via VLM."""
    
    def __init__(self):
        self._gateway = None
    
    def describe_image(self, image_bytes: bytes, question: str = "What is in this image?") -> str:
        """Send image + question to VLM, get description."""
        try:
            from scp.llm_gateway import get_gateway
            self._gateway = get_gateway()
            # Encode image as base64
            img_b64 = base64.b64encode(image_bytes).decode('utf-8')
            # Use OpenRouter vision model (llava via Ollama or GPT-4V via OpenRouter)
            # For now, use LLaVA via Ollama if available
            import urllib.request, json
            url = "http://127.0.0.1:11434/api/generate"
            payload = {
                "model": "llava",
                "prompt": question,
                "images": [img_b64],
                "stream": False,
            }
            req = urllib.request.Request(
                url, data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=30) as resp:  # nosec B310 — Ollama local VLM, validated URL
                data = json.loads(resp.read())
                return data.get("response", "")
        except Exception as e:
            logger.debug(f"VLM error: {e}")
            return ""
