#!/usr/bin/env bash
# ============================================================
# نصب خودکار سامانهٔ حضور و غیاب روی سرور (دبیان/اوبونتو)
#
# کارهایی که انجام می‌دهد:
#   1) نصب پیش‌نیازهای سیستم (کامپایلر، CMake، کتابخانه‌های dlib و ...)
#   2) ساخت venv و نصب requirements.txt
#   3) ساخت فایل .env با DJANGO_SECRET_KEY تصادفی
#   4) migrate و collectstatic
#   5) نصب و اجرای سرویس systemd (attendance.service)
#   6) باز کردن پورت در فایروال (UFW در صورت وجود)
#
# راه‌اندازی:
#   ./install_server.sh
#
# متغیرهای قابل تنظیم:
#   SRV_USER  -> کاربر اجرای سرویس (پیش‌فرض: kian)
#   PORT      -> پورت (پیش‌فرض: 8000)
#   HOST      -> آدرس بایند (پیش‌فرض: 0.0.0.0)
# ============================================================
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME="attendance"

SRV_USER="${SRV_USER:-kian}"
PORT="${PORT:-8000}"
HOST="${HOST:-0.0.0.0}"

BOLD="$(printf '\033[1m')"; GREEN="$(printf '\033[0;32m')"; RED="$(printf '\033[0;31m')"; NC="$(printf '\033[0m')"
info() { echo "${GREEN}[install]${NC} $1"; }
warn() { echo "${RED}[هشدار]${NC} $1"; }
fail() { echo "${RED}[خطا]${NC} $1"; exit 1; }

# اجرای دستور به‌عنوان کاربر سرویس (اگر ادمین هستیم)
run_as_user() {
    if [ "$(id -u)" -eq 0 ]; then
        sudo -u "$SRV_USER" "$@"
    else
        "$@"
    fi
}

command -v sudo >/dev/null 2>&1 || fail "برای ادامه به sudo نیاز داریم."
command -v python3 >/dev/null 2>&1 || fail "python3 نصب نیست."

# بهترین نسخهٔ پایتون: نسخه‌ای که برای numpy/scipy/opencv/... ویلِ ازپیش‌ساخته دارد (3.10 تا 3.12).
# در پایتون 3.13 و 3.14 این پکیج‌ها باید از سورس کامپایل شوند که هم چند گیگ دیسک می‌خواهد
# و هم بسیار کند است. اگر 3.12 موجود نبود (deadsnakes PPA) می‌شود نصبش کرد.
pick_python() {
    for py in python3.12 python3.11 python3.10; do
        if command -v "$py" >/dev/null 2>&1; then
            echo "$py"
            return 0
        fi
    done
    echo "python3"
}
PY_CMD="$(pick_python)"
info "پایتون انتخابی برای venv: $PY_CMD"

info "پوشهٔ پروژه: $PROJECT_DIR"
info "کاربر سرویس: $SRV_USER | پورت: $PORT | بایند: $HOST"

# ============================================================
echo
info "1/7) نصب پیش‌نیازهای سیستم..."
# ============================================================
sudo apt-get update -y

# پکیج‌های الزامی برای کامپایل dlib/opencv (اگر این‌ها نباشند نصب متوقف می‌شود)
sudo apt-get install -y build-essential cmake git \
    python3-venv python3-dev \
    libboost-all-dev

# پکیج‌های اختیاری/بهینه‌سازی (در بعضی نسخه‌های اوبونتو موجود نیستند؛ خطا را نادیده بگیر)
#   - libatlas-base-dev در اوبونتوهای جدید (resolute و ...) حذف شده؛ لازم هم نیست،
#     چون BLAS به‌طور پیش‌فرض OpenBLAS است (سریع‌تر و جدیدتر).
sudo apt-get install -y \
    libopenblas-dev liblapack-dev \
    libjpeg-dev zlib1g-dev \
    gfortran || true

# ============================================================
echo
info "2/7) ساخت محیط مجازی (venv)..."
# ============================================================
# اگر پایتون انتخابی 3.12/3.11/3.10 است، بسته‌های venv/dev همان نسخه را هم نصب کن
if [ "$PY_CMD" != "python3" ]; then
    sudo apt-get install -y "${PY_CMD}-venv" "${PY_CMD}-dev" || true
fi

# اگر venv فعلی با پایتون خیلی جدید (3.13 و بالاتر) ساخته شده، حذف و دوباره بساز
if [ -x "$PROJECT_DIR/venv/bin/python" ]; then
    VENV_VER="$("$PROJECT_DIR/venv/bin/python" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
    case "$VENV_VER" in
        3.10|3.11|3.12) : ;;
        *)
            warn "venv فعلی با پایتون $VENV_VER ساخته شده (بدون ویلِ آماده برای پکیج‌های سنگین). حذف و ساخت دوباره با $PY_CMD..."
            rm -rf "$PROJECT_DIR/venv"
            ;;
    esac
fi
if [ ! -d "$PROJECT_DIR/venv" ]; then
    "$PY_CMD" -m venv "$PROJECT_DIR/venv"
fi
"$PROJECT_DIR/venv/bin/pip" install --upgrade pip setuptools wheel

# ============================================================
echo
info "3/7) نصب وابستگی‌های پایتون (کامپایل dlib چند دقیقه طول می‌کشد)..."
# ============================================================
# پاک‌سازی فضای نصب‌های شکست‌خوردهٔ قبلی و کش pip (خطای Disk quota exceeded)
rm -rf /tmp/pip-* 2>/dev/null || true
"$PROJECT_DIR/venv/bin/pip" cache purge || true

FREE_KB="$(df -Pk "$PROJECT_DIR" | awk 'NR==2{print $4}')"
if [ "${FREE_KB:-0}" -lt 4000000 ]; then
    warn "فضای خالی دیسک کمتر از 4GB است (${FREE_KB}KB). اگر نصب با Disk quota exceeded متوقف شد:"
    warn "   rm -rf venv /tmp/pip-*   و سپس دوباره ./install_server.sh"
fi

"$PROJECT_DIR/venv/bin/pip" install -r "$PROJECT_DIR/requirements.txt"

# ============================================================
echo
info "4/7) ساخت فایل .env (کلید مخفی تصادفی)..."
# ============================================================
ENV_FILE="$PROJECT_DIR/.env"
if [ ! -f "$ENV_FILE" ]; then
    SECRET="$("$PROJECT_DIR/venv/bin/python" -c 'import secrets; print(secrets.token_urlsafe(50))')"
    umask 077
    cat > "$ENV_FILE" <<EOF
# تنظیمات سامانهٔ حضور و غیاب — خوانده‌شده توسط systemd (attendance.service)
DJANGO_SECRET_KEY=$SECRET
DJANGO_DEBUG=0
DJANGO_USE_HTTPS=0
DJANGO_ALLOWED_HOSTS=*
HOST=$HOST
PORT=$PORT
EOF
    info ".env ساخته شد."
else
    info ".env از قبل وجود دارد؛ تغییر ندادم. (سرویس روی پورت .env؟)"
fi

# ============================================================
echo
info "5/7) ساخت پوشهٔ دیتاست/مدل در صورت نبود و مایگریشن + استاتیک..."
# ============================================================
mkdir -p "$PROJECT_DIR/face_recognition_data/training_dataset"
run_as_user "$PROJECT_DIR/venv/bin/python" "$PROJECT_DIR/manage.py" migrate --noinput
run_as_user "$PROJECT_DIR/venv/bin/python" "$PROJECT_DIR/manage.py" collectstatic --noinput

# مالکیت فایل‌ها را برای کاربر سرویس هماهنگ کن
if [ "$(id -u)" -eq 0 ]; then
    chown -R "$SRV_USER": "$PROJECT_DIR"
fi

# ============================================================
echo
info "6/7) نصب و روشن کردن سرویس systemd..."
# ============================================================
if ! id "$SRV_USER" >/dev/null 2>&1; then
    warn "کاربر '$SRV_USER' وجود ندارد. اول بساز: sudo useradd -m -s /bin/bash $SRV_USER"
    fail "کاربر سرویس تعریف نشده است."
fi
chmod +x "$PROJECT_DIR/run_server.sh"

# تنظیم User/Group/WorkingDirectory/ExecStart داخل خود فایل سرویس با مسیر واقعی
SERVICE_FILE="$PROJECT_DIR/$SERVICE_NAME.service"
if [ -f "$SERVICE_FILE" ]; then
    sed -i "s|^User=.*|User=$SRV_USER|; s|^Group=.*|Group=$SRV_USER|" "$SERVICE_FILE"
    sed -i "s|^WorkingDirectory=.*|WorkingDirectory=$PROJECT_DIR|" "$SERVICE_FILE"
    sed -i "s|^EnvironmentFile=.*|EnvironmentFile=-$PROJECT_DIR/.env|" "$SERVICE_FILE"
    sed -i "s|^ExecStart=.*|ExecStart=$PROJECT_DIR/run_server.sh|" "$SERVICE_FILE"
    sed -i "s|Environment=PORT=.*|Environment=PORT=$PORT|" "$SERVICE_FILE"
fi

sudo cp "$SERVICE_FILE" "/etc/systemd/system/$SERVICE_NAME.service"
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME.service"
sudo systemctl restart "$SERVICE_NAME.service"

# ============================================================
echo
info "7/7) باز کردن پورت در فایروال (اختیاری)..."
# ============================================================
if command -v ufw >/dev/null 2>&1; then
    sudo ufw allow "$PORT/tcp" || true
    sudo ufw status | head -20 || true
fi

echo
echo "============================================================"
echo " ${BOLD}✅ نصب کامل شد${NC}"
echo "============================================================"
echo "  آدرس سامانه:  http://<IP-سرور>:${PORT}"
echo
echo "  دستورهای مفید:"
echo "    sudo systemctl status $SERVICE_NAME      وضعیت سرویس"
echo "    sudo systemctl restart $SERVICE_NAME     ری‌استارت"
echo "    journalctl -u $SERVICE_NAME -f           لاگ زنده"
echo "    sudo systemctl stop $SERVICE_NAME        توقف"
echo
echo "  نکته: فایل‌های ONNX مدل (face_recognition_data/*.onnx) و پوشهٔ"
echo "        training_dataset را حتماً به‌همراه پروژه منتقل کرده باشید."
echo "============================================================"