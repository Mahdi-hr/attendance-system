#!/usr/bin/env bash
# ============================================================
# اجرای ربات بله با Long Polling.
# هم برای systemd (balebot.service) و هم برای cron قابل استفاده است.
#
# نکته: flock از اجرای دو نسخهٔ هم‌زمان جلوگیری می‌کند؛ اگر ربات
# در حال اجرا باشد، این اسکریپت بی‌صدا خارج می‌شود.
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ -f .env ]; then
    set -a
    # shellcheck disable=SC1091
    . ./.env
    set +a
fi

if [ ! -x venv/bin/python ]; then
    echo "[خطا] venv پیدا نشد. ابتدا ./install_server.sh را اجرا کنید."
    exit 1
fi

exec flock -n /tmp/attendance-balebot.lock \
    venv/bin/python -u manage.py runbalebot --timeout "${BALE_POLL_TIMEOUT:-25}"
