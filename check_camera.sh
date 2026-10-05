#!/usr/bin/env bash
# ============================================================
# تشخیص وضعیت دوربین در این ماشین (قبل و بعد از USB passthrough)
#   ./check_camera.sh
#
# اگر خروجی بگوید «هیچ /dev/video* وجود ندارد»، یعنی وبکم هنوز
# داخل این VM پاس داده نشده است (راهنما: فایل CAMERA_SETUP.md).
# ============================================================
set -u

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== 1) نوع ماشین (VM یا فیزیکی) ==="
systemd-detect-virt 2>/dev/null || echo "نامشخص"

echo
echo "=== 2) دستگاه‌های /dev/video* ==="
if ls /dev/video* >/dev/null 2>&1; then
    ls -la /dev/video*
else
    echo "❌ هیچ /dev/video* وجود ندارد ← وبکم داخل این VM نیست"
fi

echo
echo "=== 3) نام دستگاه‌های v4l2 ==="
if ls /sys/class/video4linux/ >/dev/null 2>&1; then
    for f in /sys/class/video4linux/*/name; do
        [ -r "$f" ] && echo "  $f: $(cat "$f")"
    done
else
    echo "  (پوشهٔ v4l4 موجود نیست — سیستم ویدئویی هنوز فعال نشده)"
fi

echo
echo "=== 4) وبکم‌های USB ==="
if command -v lsusb >/dev/null 2>&1; then
    lsusb | grep -iE 'cam|video|webcam|imaging' || lsusb | grep -iE 'product|webcam' || echo "  (هیچ وبکمی در USB دیده نمی‌شود)"
    echo "  — لیست کامل USB:"
    lsusb
else
    echo "  lsusb در دسترس نیست"
fi

echo
echo "=== 5) ماژول کرنل uvcvideo ==="
if lsmod | grep -qE '^uvcvideo'; then
    echo "  ✓ uvcvideo لود شده"
else
    echo "  ✗ uvcvideo لود نشده (بعد از پاس‌دادن وبکم باید لود شود)"
fi

echo
echo "=== 6) دسترسی کاربر سرویس (kian) به گروه video ==="
id kian
if groups kian | grep -qw video; then
    echo "  ✓ kian عضو گروه video است"
else
    echo "  ✗ kian عضو گروه video نیست!"
    echo "    دستور:  sudo usermod -aG video kian"
fi

echo
echo "=== 7) تنظیمات CAMERA_DEVICE / CAMERA_INDEX ==="
if [ -f "$PROJECT_DIR/.env" ]; then
    set -a
    # فقط متغیرهای دوربین را می‌خوانیم
    src_dev="$(grep -E '^CAMERA_DEVICE=' "$PROJECT_DIR/.env" | cut -d= -f2-)"
    src_idx="$(grep -E '^CAMERA_INDEX=' "$PROJECT_DIR/.env" | cut -d= -f2-)"
    set +a
else
    src_dev=""; src_idx=""
fi
echo "  CAMERA_DEVICE=${src_dev:-(تنظیم نشده)}"
echo "  CAMERA_INDEX=${src_idx:-(تنظیم نشده)}"
echo "  (اگر چند دوربین دارید، یکی از این دو را در .env بگذارید و سرویس را ری‌استارت کنید)"

echo
echo "=== 8) وضعیت سرویس attendance ==="
if systemctl is-active attendance >/dev/null 2>&1; then
    echo "  ✓ سرویس در حال اجراست"
else
    echo "  ✗ سرویس اجرا نیست (sudo systemctl start attendance)"
fi

echo
echo "----------------------------------------------"
echo "خلاصه:"
if ls /dev/video* >/dev/null 2>&1; then
    echo "✅ دوربین داخل VM دیده می‌شود. اگر صفحه باز هم «در دسترس نیست» گفت،"
    echo "   مطمئن شوید کاربر kian در گروه video است و برنامهٔ دیگری دوربین را نگه نداشته."
else
    echo "❌ دوربین داخل VM دیده نمی‌شود. باید وبکم را USB passthrough کنید:"
    echo "   → راهنمای کامل: فایل CAMERA_SETUP.md"
fi