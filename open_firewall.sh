#!/usr/bin/env bash
# ============================================================
# باز کردن پورت در فایروال UFW (فقط Linux)
#   ./open_firewall.sh             -> پورت 8000
#   ./open_firewall.sh 9000        -> باز کردن پورت 9000
# ============================================================
PORT="${1:-8000}"

if ! command -v ufw >/dev/null 2>&1; then
    echo "UFW نصب نیست؛ اگر فایروال دیگری دارید همان را باز کنید."
    exit 1
fi

echo "باز کردن پورت $PORT/tcp ..."
sudo ufw allow "$PORT/tcp"
echo "---- وضعیت UFW ----"
sudo ufw status