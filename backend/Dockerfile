# VoiceFlow AI Backend Dockerfile
FROM python:3.11-slim

# Prevent python from writing pyc files and buffering stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DB_PATH=/data/voiceflow.db \
    PORT=8000

# Install ffmpeg for audio processing / stitching and curl for healthchecks
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libsndfile1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY . .

# Directory for persistent SQLite storage
RUN mkdir -p /data

EXPOSE 8000

# Run uvicorn with a SINGLE worker.
# Architecture note: The SSE broadcaster and background job worker are in-memory.
# Running multiple uvicorn workers would split SSE connections across processes.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
