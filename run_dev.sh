#!/usr/bin/env bash
# ============================================================
# اجرای سریع سرور توسعه برای تست از موبایل / شبکه
#   ./run_dev.sh                      -> 0.0.0.0:8000
#   PORT=9000 ./run_dev.sh            -> 0.0.0.0:9000
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

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"

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
    echo "       یا پورت آزاد بدهید:  PORT=9000 ./run_dev.sh"
    echo "       یا اشغالکننده را پیدا/متوقف کنید:  ss -tlnp | grep :$PORT"
    exit 1
fi

if command -v ufw >/dev/null 2>&1; then
    echo "اگر از گوشی باز نشد، فایروال (UFW) را باز کنید:  sudo ufw allow $PORT/tcp"
fi
echo "سرور توسعه روی http://$HOST:$PORT (از گوشی با آدرس IP سرور و همین پورت باز کنید)"
exec "$PYTHON" manage.py runserver "$HOST:$PORT"