#!/bin/bash
set -e

# Collect static files
echo "Collecting static files..."
python3 manage.py collectstatic --noinput

# Run migrations
echo "Running migrations..."
python3 manage.py migrate --noinput

# Create superuser if env vars are present
if [ -n "$DJANGO_SUPERUSER_USERNAME" ] && [ -n "$DJANGO_SUPERUSER_PASSWORD" ]; then
    echo "Creating superuser..."
    python3 manage.py createsuperuser --noinput --email "$DJANGO_SUPERUSER_EMAIL" || true
fi

# Run Scheduler in background if enabled (useful for single-container deployments)
if [ "$RUN_SCHEDULER" = "true" ]; then
    echo "Starting background scheduler..."
    python3 manage.py run_scheduler &
fi

# Start Gunicorn
# Use PORT from environment or default to 8282
echo "Starting Gunicorn on port ${PORT:-8282}..."
exec gunicorn --bind 0.0.0.0:${PORT:-8282} --workers 3 dca_bot.wsgi:application