# syntax=docker/dockerfile:1
FROM python:3.11-slim

# Faster, quieter, and no .pyc clutter in the image layer.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Requirements first: this layer is cached across code-only rebuilds, which is
# the difference between a 15-second and a 2-minute redeploy during a hackathon.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Run as a non-root user. Cloud Run does not require it, but a container that
# never needs root should never have it.
RUN useradd --create-home --uid 1000 appuser && chown -R appuser:appuser /app
USER appuser

ENV PORT=8080
EXPOSE 8080

# Gunicorn sizing for Cloud Run:
#   --workers 2 --threads 8  -> 16 concurrent requests per instance. The work is
#     I/O-bound (waiting on Gemini), so threads are the right lever, not processes.
#   --timeout 120  -> a real ceiling. The previous `--timeout 0` meant a hung
#     Gemini call would pin a worker forever with no recovery.
#   --graceful-timeout 30 -> let in-flight analyses finish during a revision swap.
CMD exec gunicorn --bind :$PORT \
    --workers 2 \
    --threads 8 \
    --timeout 120 \
    --graceful-timeout 30 \
    --access-logfile - \
    --error-logfile - \
    app:app
