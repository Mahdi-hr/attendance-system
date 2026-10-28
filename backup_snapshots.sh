#!/usr/bin/env bash
# ============================================================
# بکاپ روزانهٔ عکس‌های لحظه (snapshots) — برای cron.
# ۷ بکاپ آخر نگهداری می‌شود. این بکاپ جدا از بکاپ دیتابیس است و
# جلوی از دست رفتن فایل‌ها را در حوادثی مثل بازیابی/پاک‌سازی اشتباه می‌گیرد.
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

SRC="face_recognition_data/snapshots"
DEST="backups/snapshots"

[ -d "$SRC" ] || { echo "پوشهٔ snapshots وجود ندارد."; exit 0; }
mkdir -p "$DEST"

STAMP="$(date +%Y%m%d-%H%M%S)"
ARCHIVE="$DEST/snapshots-$STAMP.tar.gz"
tar -czf "$ARCHIVE" -C "face_recognition_data" snapshots

# فقط ۷ بکاپ آخر را نگه دار.
ls -1t "$DEST"/snapshots-*.tar.gz 2>/dev/null | tail -n +8 | xargs -r rm -f

echo "بکاپ عکس‌ها: $ARCHIVE ($(du -h "$ARCHIVE" | cut -f1))"
