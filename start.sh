#!/usr/bin/env bash
set -euo pipefail

# Migrate the runtime database, including on plans without a pre-deploy step.
# Stop on failure rather than serving a broken login page.
python manage.py migrate --noinput
python manage.py secure_existing_documents
python manage.py cleanup_documents --limit 50
exec gunicorn config.wsgi:application --bind "0.0.0.0:${PORT:-8000}" --access-logfile - --error-logfile -
