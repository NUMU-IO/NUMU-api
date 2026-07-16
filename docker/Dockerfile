# Build stage
FROM python:3.11-slim AS builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY pyproject.toml README.md ./
RUN pip install --upgrade pip && \
    pip install --no-cache-dir build && \
    pip wheel --no-cache-dir --wheel-dir /app/wheels -e .

# Production stage
FROM python:3.11-slim

WORKDIR /app

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    # WeasyPrint runtime dependencies (HTML/CSS -> PDF generation)
    # Ref: https://doc.courtbouillon.org/weasyprint/stable/first_steps.html#debian-ubuntu
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libpangoft2-1.0-0 \
    libgdk-pixbuf-2.0-0 \
    libcairo2 \
    libharfbuzz0b \
    fontconfig \
    && rm -rf /var/lib/apt/lists/*

# Install Arabic fonts for invoice PDF generation (Noto Sans Arabic, OFL license)
COPY docker/fonts/ /usr/share/fonts/truetype/noto-arabic/
RUN fc-cache -fv

# Create non-root user
RUN groupadd -r appgroup && useradd -r -g appgroup appuser

# Copy wheels from builder
COPY --from=builder /app/wheels /wheels
RUN pip install --no-cache-dir /wheels/*

# Ensure src is importable (needed by Alembic, Celery, etc.)
ENV PYTHONPATH=/app

# Copy application code
COPY . .

# Change ownership
RUN chown -R appuser:appgroup /app

# Switch to non-root user
USER appuser

# Expose port
EXPOSE 8000

# Health check. The API serves HTTP on :8000; celery worker/beat containers
# reuse this image with PROCESS_ROLE=celery and run no HTTP server, so the curl
# probe would fail forever and mark them (falsely) unhealthy. Skip the probe for
# celery roles — their liveness is tracked via task execution + beat heartbeat.
# `test ...` (not `[ ... ]`) so Docker doesn't parse the CMD as JSON exec form.
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD test "$PROCESS_ROLE" = "celery" || curl -f http://localhost:8000/api/v1/health || exit 1

# Run the application.
# --no-access-log: LoggingMiddleware already emits one structured line per
# request (method/path/status/duration); uvicorn's access log would duplicate
# that AND log the 30s health probe, doubling CloudWatch ingestion for no gain.
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
