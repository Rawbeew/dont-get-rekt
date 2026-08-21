# Archimeda — crypto signal engine container
#
# Multi-stage: builder installs deps into a venv, runtime copies the venv
# + source for a slim final image. Pinned versions: Python 3.12, pinned
# deps for reproducibility across builds.
#
# Build: docker build -t archimeda:latest .
# Run:   docker run --env-file .env archimeda:latest python src/run_cycle.py
#   or:  docker run --env-file .env archimeda:latest python src/run_cycle.py --watch

FROM python:3.12-slim AS builder

# Install build dependencies for any wheels that need compiling
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements first — layer-cacheable
COPY src/requirements.txt .
RUN python -m venv /app/venv \
    && /app/venv/bin/pip install --no-cache-dir --upgrade pip \
    && /app/venv/bin/pip install --no-cache-dir -r requirements.txt

# ── Runtime stage ──────────────────────────────────────────────
FROM python:3.12-slim

# Tini for clean signal handling on the GH runner / container host
RUN apt-get update && apt-get install -y --no-install-recommends tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy the prebuilt venv from builder
COPY --from=builder /app/venv /app/venv

# Copy source
COPY src/ ./src/

# Make sure venv is on PATH
ENV PATH="/app/venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# State directory for paper positions, active trades, etc.
# Mount as a volume in production: -v archimeda-state:/app/state
RUN mkdir -p /app/state

# Healthcheck: ensure Python interpreter works (real health is in the app)
HEALTHCHECK --interval=60s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import sys; sys.exit(0)" || exit 1

# Default: one cycle (suitable for cron). Override: --watch or --bot
ENTRYPOINT ["tini", "--"]
CMD ["python", "src/run_cycle.py"]
