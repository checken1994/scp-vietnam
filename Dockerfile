# SCP V88 — Koyeb deployment
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends gcc

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p data logs

EXPOSE 8000

# [ROOT-FIX] HEALTHCHECK — was checking logs/health.json (file-based, not actual HTTP)
# Now hits real /health endpoint via urllib (no curl dependency needed).
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health'); exit(0)" || exit 1

# [ROOT-FIX] CMD — previous entry script was missing (container crashed on boot).
# Now uses start_api.py (verified entry point) binding 0.0.0.0:8000.
CMD ["python", "start_api.py", "--host", "0.0.0.0", "--port", "8000"]
