#!/usr/bin/env bash
# ============================================================
# اجرای تولیدی سامانهٔ حضور و غیاب — نسخهٔ سریع (بدون migrate/collectstatic)
# این اسکریپت را systemd (attendance.service) صدا می‌زند.
#
# نکتهٔ معماری مهم:
# وضعیت دوربین و تشخیص چهره (camera_manager) داخل خودِ سامانه یک نمونهٔ
# درون‌فرایندی است؛ اگر بیش از یک worker داشته باشیم هر worker وضعیت جداگانه‌ای
# دارد و ورود/خروج/استریم دوربین در بین درخواست‌ها به هم می‌خورد.
# بنابراین Workers=1 + Threads=8 است که هم درست کار کند هم بهینه باشد.
#
# متغیرها می‌توانند از EnvironmentFile (فایل .env) یا محیط صدا زننده بیایند:
#   DJANGO_SECRET_KEY   DJANGO_DEBUG   DJANGO_USE_HTTPS   DJANGO_ALLOWED_HOSTS
#   HOST  PORT  GUNICORN_WORKERS  GUNICORN_THREADS  GUNICORN_TIMEOUT
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

VENV="$SCRIPT_DIR/venv"
PYTHON="$VENV/bin/python"

if [ ! -x "$PYTHON" ]; then
    echo "[خطا] پوشهٔ venv پیدا نشد. ابتدا: ./install_server.sh"
    exit 1
fi

# ---------- تنظیمات محیط ----------
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"

export DJANGO_DEBUG="${DJANGO_DEBUG:-0}"
export DJANGO_USE_HTTPS="${DJANGO_USE_HTTPS:-0}"
export DJANGO_ALLOWED_HOSTS="${DJANGO_ALLOWED_HOSTS:-*}"

if [ -z "${DJANGO_SECRET_KEY:-}" ]; then
    echo "[هشدار] DJANGO_SECRET_KEY تنظیم نشده؛ یک کلید موقت و تصادفی ساخته می‌شود."
    echo "[هشدار] با این کلید نشست‌ها با هر ری‌استارت بی‌اعتبار می‌شوند."
    echo "[هشدار] برای تولید، در .env مقدار DJANGO_SECRET_KEY را تنظیم کنید."
    export DJANGO_SECRET_KEY="django-insecure-$(head -c 32 /dev/urandom | base64 | tr -d '=+/')"
fi

WORKERS="${GUNICORN_WORKERS:-1}"                     # نکتهٔ معماری بالا
THREADS="${GUNICORN_THREADS:-8}"
TIMEOUT="${GUNICORN_TIMEOUT:-120}"                   # مدل چهره گاهی کند است
MAX_REQUESTS="${GUNICORN_MAX_REQUESTS:-1000}"

# ---------- اجرا ----------
echo "اجرای Gunicorn روی $HOST:$PORT (workers=$WORKERS threads=$THREADS)..."
exec "$VENV/bin/gunicorn" \
    attendance_system_facial_recognition.wsgi:application \
    --bind "$HOST:$PORT" \
    --workers "$WORKERS" \
    --worker-class gthread \
    --threads "$THREADS" \
    --timeout "$TIMEOUT" \
    --graceful-timeout 30 \
    --keep-alive 5 \
    --max-requests "$MAX_REQUESTS" \
    --max-requests-jitter 100 \
    --access-logfile - \
    --error-logfile - \
    --chdir "$SCRIPT_DIR"
