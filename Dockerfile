# Stage 1: Build stage
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies to a temporary directory
COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install --no-cache-dir --prefix=/install -r requirements.txt

# Stage 2: Final production image
FROM python:3.12-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DJANGO_SETTINGS_MODULE=dca_bot.settings.production

WORKDIR /app

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user and necessary directories
RUN useradd -m -r appuser && \
    mkdir -p /app/static /app/media /app/logs && \
    chown -R appuser:appuser /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application code
COPY --chown=appuser:appuser . .

# Ensure entrypoint is executable
RUN chmod +rx /app/entrypoint.sh

# Switch to non-root user
USER appuser

# Expose the application port
EXPOSE 8282

# Use entrypoint script to handle startup tasks
ENTRYPOINT ["/app/entrypoint.sh"]