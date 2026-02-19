#!/bin/bash
set -e

# Run migrations
echo "Running migrations..."
uv run python manage.py migrate

# Create superuser if env vars are present
if [ -n "$DJANGO_SUPERUSER_USERNAME" ] && [ -n "$DJANGO_SUPERUSER_PASSWORD" ]; then
    echo "Creating superuser..."
    uv run python manage.py createsuperuser --noinput --email "$DJANGO_SUPERUSER_EMAIL" || true
fi

# Run Scheduler in background if enabled (useful for single-container deployments)
if [ "$RUN_SCHEDULER" = "true" ]; then
    echo "Starting background scheduler..."
    uv run python manage.py run_scheduler &
fi

# Start server
echo "Starting server..."
exec "$@"
