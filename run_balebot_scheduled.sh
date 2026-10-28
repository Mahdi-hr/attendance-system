#!/usr/bin/env bash
# ارسال پیام‌های زمان‌بندی‌شده — برای cron هر دقیقه.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
if [ -f .env ]; then
    set -a
    # shellcheck disable=SC1091
    . ./.env
    set +a
fi
exec flock -n /tmp/attendance-balebot-scheduled.lock \
    ./venv/bin/python manage.py balebot_send_scheduled
