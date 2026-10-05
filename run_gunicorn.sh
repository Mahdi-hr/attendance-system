#!/usr/bin/env bash
# ============================================================
# اجرای سامانهٔ حضور و غیاب با Gunicorn — بهینه برای سرور
#
# نکتهٔ معماری مهم:
# وضعیت دوربین و تشخیص چهره (camera_manager) داخل خودِ سامانه یک نمونهٔ
# درون‌فرایندی است؛ اگر بیش از یک worker داشته باشیم هر worker وضعیت جداگانه‌ای
# دارد و ورود/خروج/استریم دوربین در بین درخواست‌ها به هم می‌خورد.
# بنابراین پیش‌فرض Workers=1 + Threads=8 است که هم درست کار کند هم از نظر
# همزمانی (گزارش‌ها و پاسخ‌های طولانی) بهینه باشد.
#
# استفاده:
#   ./run_gunicorn.sh                 # اجرای روی 0.0.0.0:8000 با تنظیمات پیش‌فرض
#   PORT=80 ./run_gunicorn.sh         # تغییر پورت
#   HOST=127.0.0.1 GUNICORN_THREADS=16 ./run_gunicorn.sh
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

VENV="$SCRIPT_DIR/venv"
PYTHON="$VENV/bin/python"

if [ ! -x "$PYTHON" ]; then
    echo "[خطا] پوشهٔ venv پیدا نشد. ابتدا: python3 -m venv venv && venv/bin/pip install -r requirements.txt"
    exit 1
fi

# ---------- متغیرهای قابل تنظیم ----------
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
export DJANGO_DEBUG="${DJANGO_DEBUG:-0}"            # production: خاموش
# ⚠️ هیچ کلید ثابتی در کد نگذار. کلید باید از .env یا EnvironmentFile بیاید.
if [ -z "${DJANGO_SECRET_KEY:-}" ]; then
    echo "[هشدار] DJANGO_SECRET_KEY تنظیم نشده؛ یک کلید موقت و تصادفی ساخته می‌شود." >&2
    echo "[هشدار] با این کلید نشست‌ها با هر ری‌استارت بی‌اعتبار می‌شوند." >&2
    echo "[هشدار] برای تولید، در .env مقدار DJANGO_SECRET_KEY را تنظیم کنید." >&2
    export DJANGO_SECRET_KEY="django-insecure-$(head -c 32 /dev/urandom | base64 | tr -d '=+/')"
fi
# ALLOWED_HOSTS به‌صورت پیش‌فرض '*' است؛ برای محدودکردن:
# export DJANGO_ALLOWED_HOSTS="example.com,192.168.1.10"

WORKERS="${GUNICORN_WORKERS:-1}"                     # ببینید توضیح بالا
THREADS="${GUNICORN_THREADS:-8}"
TIMEOUT="${GUNICORN_TIMEOUT:-120}"                   # مدل چهره گاهی کند است
MAX_REQUESTS="${GUNICORN_MAX_REQUESTS:-1000}"

# ---------- بررسی آزاد بودن پورت ----------
if "$PYTHON" - "$PORT" <<'PY'
import socket, sys
try:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", int(sys.argv[1])))
except OSError:
    sys.exit(2)
PY
then
    : # پورت آزاد است
else
    echo "[خطا] پورت $PORT اشغال است (پروسهٔ دیگری روی آن در حال اجراست)."
    echo "       یا پورت آزاد بدهید:  PORT=9000 ./run_gunicorn.sh"
    echo "       یا اشغالکننده را پیدا/متوقف کنید:  ss -tlnp | grep :$PORT"
    exit 1
fi

# ---------- پیش‌نیازهای هر اجرا ----------
echo "[1/3] اعمال میگریشن‌ها..."
"$PYTHON" manage.py migrate --noinput

echo "[2/3] جمع‌آوری فایل‌های استاتیک (WhiteNoise)..."
"$PYTHON" manage.py collectstatic --noinput --clear

echo "[3/3] اجرای Gunicorn روی $HOST:$PORT (workers=$WORKERS threads=$THREADS)..."
if command -v ufw >/dev/null 2>&1; then
    echo "       اگر از گوشی باز نشد، فایروال (UFW) را باز کنید:  sudo ufw allow $PORT/tcp"
fi
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