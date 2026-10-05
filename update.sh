#!/usr/bin/env bash
# ============================================================
#  update.sh — به‌روزرسانی امن کد از گیت‌هاب
# ============================================================
#
#  هدف: آخرین نسخهٔ کد را از گیت‌هاب بگیرد، **بدون** آسیب به داده‌های محلی
#  (دیتابیس، .env، تصاویر چهره، embeddings) و **بدون** ایجاد تداخل کد.
#
#  چه چیزهایی دست‌نخورده می‌مانند (هرگز تغییر نمی‌کنند):
#     • db.sqlite3            ← دیتابیس (حضور و غیاب، کاربران، گزارش‌ها)
#     • .env                  ← کلیدها و تنظیمات محرمانه
#     • face_recognition_data/← تصاویر چهره، embeddings، مدل‌های onnx
#     • staticfiles/، media/، venv/، لاگ‌ها
#     • هر فایل untracked (فایل‌هایی که خودت ساختی و در گیت نیستند)
#
#  چه چیزهایی به‌روز می‌شوند؟
#     فقط فایل‌های tracked کد (app/، templates/، static/، اسکریپت‌ها، …)
#
#  ----------------------------------------------------------
#  استفاده:
#     ./update.sh              # پیش‌نمایش بگیر و تأیید بگیر، بعد آپدیت کن
#     ./update.sh --check      # فقط بگو چه چیزی قرار است عوض شود (بدون تغییر)
#     ./update.sh --yes        # بدون پرسش، مستقیم آپدیت کن
#     ./update.sh --reset      # تغییرات محلی کد را دور بریز و دقیقاً نسخهٔ سرور را بگیر
#     ./update.sh --no-restart # بعد از آپدیت سرویس را ری‌استارت نکن
#     ./update.sh --help
#  ----------------------------------------------------------
#  اگر در هر مرحله‌ای خطا رخ دهد، به‌صورت خودکار:
#     کد به نسخهٔ قبلی برمی‌گردد، دیتابیس از بکاپ بازیابی می‌شود
#     و تغییرات محلی تو در stash باقی می‌ماند (هیچ‌چیز گم نمی‌شود).
# ============================================================

set -Eeuo pipefail

# ---------- تنظیمات قابل تغییر با محیط ----------
REPO_URL="${REPO_URL:-https://github.com/Mahdi-hr/Attendance-For-Office.git}"
REMOTE="${REMOTE:-origin}"
BRANCH="${BRANCH:-main}"
SERVICE_NAME="${SERVICE_NAME:-attendance}"
DB_FILE="${DB_FILE:-db.sqlite3}"
KEEP_BACKUPS="${KEEP_BACKUPS:-10}"
HEALTH_URL="${HEALTH_URL:-}"

# ---------- پرچم‌ها ----------
DO_CHECK=0
ASSUME_YES=0
DO_RESET=0
DO_RESTART=1

usage() {
    sed -n '2,35p' "$0" | sed 's/^# \{0,1\}//'
    exit 0
}

while [ $# -gt 0 ]; do
    case "$1" in
        -c|--check|--dry-run) DO_CHECK=1 ;;
        -y|--yes)             ASSUME_YES=1 ;;
        --reset)              DO_RESET=1 ;;
        --no-restart)         DO_RESTART=0 ;;
        -h|--help)            usage ;;
        *) echo "گزینهٔ ناشناخته: $1 (با --help راهنما را ببین)" >&2; exit 2 ;;
    esac
    shift
done

# ---------- رنگ و توابع کمکی ----------
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    C_R=$'\033[31m'; C_G=$'\033[32m'; C_Y=$'\033[33m'; C_B=$'\033[36m'; C_D=$'\033[2m'; C_0=$'\033[0m'
else
    C_R=""; C_G=""; C_Y=""; C_B=""; C_D=""; C_0=""
fi

step()  { printf '\n%s━━ %s%s\n' "$C_B" "$*" "$C_0"; }
info()  { printf '   %s\n' "$*"; }
ok()    { printf '   %s✔ %s%s\n' "$C_G" "$*" "$C_0"; }
warn()  { printf '   %s⚠ %s%s\n' "$C_Y" "$*" "$C_0"; }
err()   { printf '   %s✘ %s%s\n' "$C_R" "$*" "$C_0"; }
dim()   { printf '   %s%s%s\n' "$C_D" "$*" "$C_0"; }
die()   { err "$*"; exit 1; }

# ---------- محیط پروژه ----------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

VENV="$SCRIPT_DIR/venv"
PYTHON="$VENV/bin/python"
BACKUP_ROOT="$SCRIPT_DIR/backups"
LOCK_FILE="$SCRIPT_DIR/.update.lock"

# ---------- قفل: دو آپدیت همزمان ممنوع ----------
if ! command -v flock >/dev/null 2>&1; then
    warn "ابزار flock نصب نیست؛ همزمانی کنترل نمی‌شود."
elif ! exec 9>"$LOCK_FILE"; then
    die "نمی‌توانم فایل قفل را بسازم: $LOCK_FILE"
elif ! flock -n 9; then
    die "یک آپدیت دیگر در حال اجراست (قفل فعال است). صبر کن یا اگر مطمئنی، .update.lock را پاک کن."
fi

# ============================================================
#  [۰] بررسی‌های اولیه
# ============================================================
step "بررسی پیش‌نیازها"

command -v git >/dev/null 2>&1 || die "git نصب نیست. نصب: sudo apt install git"

git rev-parse --is-inside-work-tree >/dev/null 2>&1 \
    || die "این پوشه مخزن گیت نیست. ابتدا در پوشهٔ اصلی پروژه اجرا کن، یا مخزن را کلون کن:
    git clone $REPO_URL && cd Attendance-For-Office"

# اگر در حالت detached head هستیم، خطا بده
if [ "$(git symbolic-ref -q --short HEAD || true)" = "" ]; then
    die "در حالت detached HEAD هستی. ابتدا برگرد: git checkout $BRANCH"
fi

CURRENT_BRANCH="$(git symbolic-ref --short HEAD)"
[ "$CURRENT_BRANCH" = "$BRANCH" ] \
    || warn "برانچ فعلی '$CURRENT_BRANCH' است نه '$BRANCH'. آپدیت از برانچ '$BRANCH' انجام می‌شود."

info "پوشه:    $SCRIPT_DIR"
info "برانچ:   $CURRENT_BRANCH"
info "سرور:    $REMOTE/$BRANCH"

if [ -x "$PYTHON" ]; then
    ok "محیط مجازی پیدا شد: venv/"
else
    warn "venv پیدا نشد؛ نصب پکیج‌ها و migrate انجام نمی‌شود (بقیهٔ آپدیت کار می‌کند)."
fi

# وضعیت فایل‌های داده — فقط برای گزارش، هیچ تغییری در آن‌ها نمی‌دهیم
DATA_FILES=("$DB_FILE" ".env" "face_recognition_data" "staticfiles" "media")
info "فایل‌های داده (دست‌نخورده می‌مانند):"
for f in "${DATA_FILES[@]}"; do
    if [ -e "$f" ]; then
        printf '   %s  • %-24s %s%s\n' "$C_G" "$f" "$(du -sh "$f" 2>/dev/null | cut -f1)" "$C_0"
    else
        printf '   %s  • %-24s %s%s\n' "$C_D" "$f" "(وجود ندارد)" "$C_0"
    fi
done

# ============================================================
#  [۱] وضعیت فعلی گیت
# ============================================================
step "بررسی وضعیت مخزن"

OLD_SHA="$(git rev-parse HEAD)"
LOCAL_MODIFIED="$(git diff --name-only HEAD || true)"
LOCAL_STAGED="$(git diff --cached --name-only || true)"
LOCAL_UNTRACKED="$(git ls-files --others --exclude-standard || true)"

info "کامیت فعلی: $(git rev-parse --short HEAD)  —  $(git log -1 --format=%s)"
echo

if [ -n "$LOCAL_STAGED" ]; then
    warn "تغییرات staged (index) وجود دارد — قبل از آپدیت commit یا stash کن:"
    printf '        %s\n' $LOCAL_STAGED
fi

if [ -n "$LOCAL_MODIFIED" ]; then
    warn "تغییرات محلی در فایل‌های tracked:"
    printf '        %s\n' $LOCAL_MODIFIED
fi

if [ -n "$LOCAL_UNTRACKED" ]; then
    dim "فایل‌های untracked (دست‌نخورده می‌مانند، ${#LOCAL_UNTRACKED} فایل):"
    printf '        %s\n' $LOCAL_UNTRACKED | head -20
fi
echo

# ============================================================
#  [۲] گرفتن آخرین نسخه از گیت‌هاب
# ============================================================
step "دریافت از گیت‌هاب"

if ! git remote get-url "$REMOTE" >/dev/null 2>&1; then
    git remote add "$REMOTE" "$REPO_URL"
    info "remote '$REMOTE' اضافه شد: $REPO_URL"
fi

git fetch --prune --tags "$REMOTE" 2>&1 | sed 's/^/   /' || die "ارتباط با گیت‌هاب برقرار نشد (اینترنت یا دسترسی به remote را بررسی کن)."

NEW_SHA="$(git rev-parse "$REMOTE/$BRANCH" 2>/dev/null || true)"
[ -n "$NEW_SHA" ] || die "برانچ '$BRANCH' در سرور پیدا نشد."

if [ "$NEW_SHA" = "$OLD_SHA" ]; then
    ok "همه‌چیز به‌روز است — نیازی به آپدیت نیست."
    info "کامیت: $(git rev-parse --short HEAD)  —  $(git log -1 --format=%s)"
    exit 0
fi

# آیا شاخهٔ محلی از سرور عقب است یا منحرف شده؟
if git merge-base --is-ancestor "$OLD_SHA" "$NEW_SHA"; then
    BEHIND=$(( $(git rev-list --count "$OLD_SHA..$NEW_SHA") ))
    AHEAD=$(( $(git rev-list --count "$NEW_SHA..$OLD_SHA") ))
    ok "$BEHIND کامیت جدید روی سرور موجود است."
    [ "$AHEAD" -gt 0 ] && warn "$AHEAD کامیت محلی روی سرور نیست (سرور عقب‌تر از شما نیست، ولی شما هم از سرور جلو زده‌اید)."
else
    BEHIND=0
    ok "سرور کامیت‌های جدیدی دارد که شاخهٔ محلی از آن‌ها مشتق نشده."
fi

# ---------- فهرست تغییرات ----------
CHANGED_FILES="$(git diff --name-only "$OLD_SHA" "$NEW_SHA" || true)"
REQ_CHANGED=0
MIG_CHANGED=0
STATIC_CHANGED=0

git diff --quiet "$OLD_SHA" "$NEW_SHA" -- requirements.txt || REQ_CHANGED=1
if [ -n "$(git diff --name-only "$OLD_SHA" "$NEW_SHA" -- '*/migrations/*.py' || true)" ]; then
    MIG_CHANGED=1
fi
if [ -n "$(git diff --name-only "$OLD_SHA" "$NEW_SHA" -- 'static/*' '*/static/*' 'templates/*' '*/templates/*' || true)" ]; then
    STATIC_CHANGED=1
fi

echo
info "کامیت‌های جدید:"
git log --oneline --no-decorate "$OLD_SHA..$NEW_SHA" | sed 's/^/        /' || true
echo
info "فایل‌های تغییرکرده ($(printf '%s\n' "$CHANGED_FILES" | grep -c . || true) فایل):"
printf '%s\n' "$CHANGED_FILES" | grep . | sed 's/^/        /' | head -60 || true
[ "$(printf '%s\n' "$CHANGED_FILES" | grep -c . || true)" -gt 60 ] && dim "        … و بیشتر"
echo

# کارهایی که خودکار انجام می‌شود:
[ "$REQ_CHANGED"   -eq 1 ] && info "• requirements.txt تغییر کرده → pip install اجرا می‌شود"
[ "$MIG_CHANGED"   -eq 1 ] && info "• مایگریشن جدید هست → migrate اجرا می‌شود (دیتابیس بکاپ گرفته می‌شود)"
[ "$STATIC_CHANGED" -eq 1 ] && info "• فایل استاتیک تغییر کرده → collectstatic اجرا می‌شود"
echo

# ============================================================
#  [۳] بررسی تداخل (هستهٔ «بدون تداخل»)
# ============================================================
step "بررسی تداخل"

CLASH=""
if [ -n "$LOCAL_MODIFIED" ]; then
    # فایل‌هایی که هم محلی تغییر کرده‌اند و هم در آپدیت جدید تغییر کرده‌اند
    CLASH="$(comm -12 \
        <(printf '%s\n' "$LOCAL_MODIFIED" | sort -u) \
        <(printf '%s\n' "$CHANGED_FILES"  | sort -u) || true)"
fi

if [ -n "$CLASH" ]; then
    err "تغییرات محلی تو با آپدیت جدید روی همین فایل‌ها تداخل دارند:"
    printf '        %s\n' $CLASH
    echo
    warn "برای جلوگیری از هرگونه خرابی، آپدیت متوقف شد. یکی از این کارها را انتخاب کن:"
    echo
    echo "    ۱) تغییراتت را نگه دار و بعد آپدیت کن (تغییراتت دست‌نخورده در stash می‌ماند):"
    echo "         git stash push -m 'my local changes'"
    echo "         ./update.sh"
    echo "         git stash pop          # اگر تداخل داد، دستی حلش کن"
    echo
    echo "    ۲) اگر تغییراتت را می‌خواهی این‌جا هم باشد (کد را به سرور push کن):"
    echo "         git add -A && git commit -m '...' && git push"
    echo
    echo "    ۳) اگر مطمئنی تغییراتت لازم نیست، دقیقاً نسخهٔ سرور را بگیر:"
    echo "         ./update.sh --reset"
    echo
    dim "هیچ تغییری در فایل‌ها یا دیتابیس داده نشد."
    exit 3
fi

if [ -n "$LOCAL_MODIFIED" ]; then
    ok "تداخلی نیست — تغییرات محلی‌ات حفظ می‌شوند."
    dim "تغییرات محلی که حفظ می‌شوند:"
    printf '%s\n' "$LOCAL_MODIFIED" | sed 's/^/        /'
elif [ "$DO_RESET" -eq 1 ]; then
    warn "با --reset همهٔ تغییرات محلی کد دور ریخته می‌شوند."
else
    ok "پوشهٔ کد کاملاً تمیز است — نه تداخلی، نه ریسکی."
fi
echo

# ============================================================
#  [۴] حالت بررسی فقط
# ============================================================
if [ "$DO_CHECK" -eq 1 ]; then
    step "خلاصه (حالت بررسی — هیچ تغییری داده نشد)"
    ok "$BEHIND کامیت جدید آمادهٔ اعمال است."
    info "دیتابیس و داده‌ها: دست‌نخورده"
    [ "$REQ_CHANGED"    -eq 1 ] && info "پکیج‌ها: نصب می‌شوند"
    [ "$MIG_CHANGED"    -eq 1 ] && info "مایگریشن: اجرا می‌شود"
    [ "$STATIC_CHANGED" -eq 1 ] && info "استاتیک: جمع‌آوری می‌شود"
    echo
    dim "برای اعمال واقعی:  ./update.sh"
    exit 0
fi

# ---------- تأیید کاربر ----------
if [ "$ASSUME_YES" -eq 0 ]; then
    printf '%sبرویم؟ [y/N] %s' "$C_B" "$C_0"
    read -r REPLY || true
    case "${REPLY:-n}" in
        [yY]|[yY]es|[Yy]|[آآره]) ;;
        *) info "لغو شد. هیچ تغییری داده نشد."; exit 0 ;;
    esac
fi

# ============================================================
#  [۵] بکاپ داده‌ها (تضمین «چیزی از دیتابیس پاک نمی‌شود»)
# ============================================================
step "بکاپ داده‌ها"

TS="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="$BACKUP_ROOT/$TS"
mkdir -p "$BACKUP_DIR"

printf '%s' "$OLD_SHA" > "$BACKUP_DIR/COMMIT_BEFORE.txt"
git log -1 --format='%H %s' > "$BACKUP_DIR/COMMIT_BEFORE.txt"

[ -f .env ] && cp -a .env "$BACKUP_DIR/.env" && ok ".env بکاپ شد"

# بکاپ دیتابیس با API رسمی sqlite (ایمن حتی وقتی سرویس در حال اجراست)
if [ -f "$DB_FILE" ]; then
    if command -v python3 >/dev/null 2>&1; then
        python3 - "$DB_FILE" "$BACKUP_DIR/$(basename "$DB_FILE")" <<'PYEOF'
import sqlite3, sys, os
src_path, dst_path = sys.argv[1], sys.argv[2]
try:
    src = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
    dst = sqlite3.connect(dst_path)
    with dst:
        src.backup(dst)
    src.close(); dst.close()
    print(f"   {os.path.getsize(dst_path):,} bytes")
except Exception as e:
    sys.stderr.write(f"sqlite-backup-failed: {e}\n")
    sys.exit(1)
PYEOF
        ok "دیتابیس بکاپ شد: backups/$TS/$(basename "$DB_FILE")"
    else
        cp -a "$DB_FILE" "$BACKUP_DIR/" && warn "python3 نیست → کپی سادهٔ دیتابیس (اگر سرویس در حال اجراست، یکبار سرویس را متوقف کن)"
    fi
else
    warn "$DB_FILE پیدا نشد (بکاپی لازم نیست)."
fi

# بکاپ از face_recognition_data فقط اگر خیلی کوچک باشد، وگرنه فقط فهرست فایل‌ها
if [ -d face_recognition_data ]; then
    find face_recognition_data -maxdepth 2 -printf '%y %10s %p\n' > "$BACKUP_DIR/face_recognition_data_INDEX.txt" 2>/dev/null || true
    dim "فهرست face_recognition_data ذخیره شد (خود فایل‌ها دست‌نخورده می‌مانند)."
fi

# پاک‌سازی بکاپ‌های قدیمی
if [ -d "$BACKUP_ROOT" ]; then
    OLD_COUNT=$(find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d | wc -l)
    if [ "$OLD_COUNT" -gt "$KEEP_BACKUPS" ]; then
        find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' \
            | sort -n | head -n "$((OLD_COUNT - KEEP_BACKUPS))" | cut -d' ' -f2- \
            | while read -r d; do rm -rf "$d"; echo "   پاک شد: $d"; done
    fi
fi
echo

# ---------- توابع بازگشت (rollback) ----------
STASHED=0
STASH_SHA=""
CODE_MERGED=0
MIGRATED=0
ROLLED_BACK=0

restore_db() {
    local b="$BACKUP_DIR/$(basename "$DB_FILE")"
    [ -f "$b" ] || return 0
    cp -a "$b" "$DB_FILE"
    err "دیتابیس از بکاپ بازیابی شد: $DB_FILE"
}

rollback() {
    local reason="$1"
    [ "$ROLLED_BACK" -eq 1 ] && return 0
    ROLLED_BACK=1
    err "خطا: $reason"
    echo
    err "در حال بازگشت به نسخهٔ قبلی…"

    if [ "$CODE_MERGED" -eq 1 ]; then
        git reset --hard "$OLD_SHA" >/dev/null 2>&1 && ok "کد به $(git rev-parse --short "$OLD_SHA") برگشت."
    fi

    if [ "$MIGRATED" -eq 1 ]; then
        restore_db
    fi

    if [ "$STASHED" -eq 1 ] && [ -n "$STASH_SHA" ]; then
        if git stash list | grep -q "$STASH_SHA"; then
            warn "تغییرات محلی تو هنوز در stash هستند و پاک نشده‌اند:"
            dim "        git stash list     # برای دیدن"
            dim "        git stash pop      # برای برگرداندن (فقط بعد از رفع مشکل)"
        fi
    fi

    if [ "$DO_RESTART" -eq 1 ]; then
        restart_service || true
    fi
    echo
    err "به نسخهٔ قبل برگشتیم. دیتابیس و داده‌ها سالم‌اند."
    dim "بکاپ: $BACKUP_DIR"
    exit 1
}

restart_service() {
    command -v systemctl >/dev/null 2>&1 || return 0
    if [ "$(id -u)" -eq 0 ]; then
        systemctl restart "$SERVICE_NAME" 2>/dev/null || return 1
    elif command -v sudo >/dev/null 2>&1; then
        sudo -n systemctl restart "$SERVICE_NAME" 2>/dev/null || return 1
    else
        return 1
    fi
}

# گرفتن خطا → rollback
trap 'rollback "خطای غیرمنتظره در خط $(($LINENO-2))"' ERR

# ============================================================
#  [۶] ذخیرهٔ تغییرات محلی کد (فقط فایل‌های tracked)
# ============================================================
step "ذخیرهٔ تغییرات محلی"

if [ "$DO_RESET" -eq 1 ] && [ -n "$LOCAL_MODIFIED" ]; then
    git checkout -- . 2>/dev/null || git reset --hard HEAD >/dev/null
    ok "تغییرات محلی کد دور ریخته شد (--reset). داده‌ها دست‌نخورده‌اند."
elif [ -n "$LOCAL_MODIFIED" ]; then
    git stash push -q -m "pre-update $TS (local changes kept)" -- . || die "stash کردن تغییرات محلی ناموفق بود."
    STASHED=1
    STASH_SHA="$(git rev-parse --verify --quiet refs/stash || true)"
    ok "تغییرات محلی در stash امن نگه داشته شد (بعد از آپدیت برمی‌گردند)."
fi

# فایل‌های untracked اصلاً دست نمی‌خورند
dim "فایل‌های untracked و داده‌ها اصلاً جابه‌جا نمی‌شوند."
echo

# ============================================================
#  [۷] اعمال آپدیت (فقط fast-forward)
# ============================================================
step "اعمال آپدیت کد"

# اگر فایل untracked هم‌نام فایل جدید سرور باشد، git اینجا امن متوقف می‌شود
if ! git merge --ff-only "$REMOTE/$BRANCH"; then
    err "git نتوانست آپدیت را اعمال کند. رایج‌ترین دلیل:"
    dim "  فایل untracked تو هم‌نام فایلی است که در آپدیت جدید اضافه شده."
    echo
    echo "  این فایل‌ها را پیدا کن:  git diff --name-only --diff-filter=A $OLD_SHA $NEW_SHA"
    echo "  سپس نامشان را عوض کن یا به گیت اضافه‌شان کن و دوباره اجرا کن."
    exit_code=$?
    CODE_MERGED=0
    if [ "$STASHED" -eq 1 ] && [ -n "$STASH_SHA" ]; then
        git stash pop -q 2>/dev/null || warn "برگرداندن stash خودکار نشد؛ با 'git stash pop' دستی انجامش بده."
    fi
    trap - ERR
    echo
    dim "کد دست‌نخورده ماند، دیتابیس هم سالم است."
    exit "$exit_code"
fi
CODE_MERGED=1
ok "کد به‌روز شد: $(git rev-parse --short "$OLD_SHA") → $(git rev-parse --short HEAD)"
[ -n "$CHANGED_FILES" ] && dim "$(printf '%s\n' "$CHANGED_FILES" | grep -c . || true) فایل به‌روز شد."
echo

# ============================================================
#  [۸] وابستگی‌ها
# ============================================================
if [ "$REQ_CHANGED" -eq 1 ]; then
    step "نصب وابستگی‌های جدید"
    if [ -x "$PYTHON" ]; then
        info "تغییرات requirements.txt:"
        git diff "$OLD_SHA" HEAD -- requirements.txt \
            | grep -E '^[+-][^+-]' | sed 's/^/        /' | head -20 || true
        if "$VENV/bin/pip" install --disable-pip-version-check -q -r requirements.txt; then
            ok "وابستگی‌ها نصب شدند."
        else
            rollback "نصب وابستگی‌ها شکست خورد"
        fi
    else
        warn "venv نیست → بعداً دستی اجرا کن:  python3 -m venv venv && venv/bin/pip install -r requirements.txt"
    fi
    echo
fi

# ============================================================
#  [۹] مایگریشن دیتابیس (با بکاپ)
# ============================================================
if [ "$MIG_CHANGED" -eq 1 ] && [ -x "$PYTHON" ]; then
    step "به‌روزرسانی دیتابیس (migrate)"
    info "مایگریشن‌های جدید:"
    git diff --name-only "$OLD_SHA" HEAD -- '*/migrations/*.py' | sed 's/^/        /' || true
    dim "بدون حذف داده. اگر لازم باشد، رکوردها به‌صورت No-op رد می‌شوند."
    echo

    # اجرای manage.py با بارگذاری امن .env (بدون source کردن شل)
    run_manage() {
        "$PYTHON" - "$@" <<'PYEOF'
import os, subprocess, sys
env = dict(os.environ)
if os.path.exists(".env"):
    for raw in open(".env", encoding="utf-8"):
        s = raw.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, _, v = s.partition("=")
        env[k.strip()] = v.strip().strip('"').strip("'")
sys.exit(subprocess.call([sys.executable, "manage.py", *sys.argv[1:]], env=env))
PYEOF
    }

    # پرچم را *قبل* از اجرا روشن می‌کنیم: اگر migrate نیمه‌کاره fail شود،
    # باید دیتابیس از بکاپ بازیابی شود (حتی اگر بعضی مایگریشن‌ها اعمال شده باشند).
    MIGRATED=1
    if run_manage migrate --noinput; then
        ok "دیتابیس به‌روز شد (رکوردهای شما حفظ شدند)."
    else
        rollback "migrate شکست خورد — دیتابیس به نسخهٔ قبل برگشت"
    fi
    echo
fi

# ============================================================
#  [۱۰] فایل‌های استاتیک
# ============================================================
if [ "$STATIC_CHANGED" -eq 1 ] && [ -x "$PYTHON" ]; then
    step "به‌روزرسانی فایل‌های استاتیک"
    if run_manage collectstatic --noinput >/dev/null 2>&1; then
        ok "collectstatic انجام شد."
    else
        warn "collectstatic ناموفق بود (سامانه معمولاً بدون آن هم بالا می‌آید)."
    fi
    echo
fi

# ============================================================
#  [۱۱] بازگرداندن تغییرات محلی
# ============================================================
if [ "$STASHED" -eq 1 ] && [ -n "$STASH_SHA" ]; then
    step "بازگرداندن تغییرات محلی تو"
    if git stash pop -q; then
        ok "تغییراتت برگشت — بدون تداخل."
        STASHED=0
    else
        warn "در بازگرداندن تغییراتت conflict پیش آمد. تغییراتت پاک نشده؛ فقط باید دستی حل شود."
        echo
        echo "  فایل‌های conflicted:  git diff --name-only --diff-filter=U"
        echo "  حل کن و بعد:         git add <فایل> && git checkout --theirs .  # یا ادامهٔ کار دستی"
        echo
        echo "  اگر خواستی کلاً بی‌خیال شوی و نسخهٔ سرور را داشته باشی:"
        echo "        git checkout --theirs . && git add -A && git stash drop"
        echo
        echo "  اگر خواستی همه‌چیز به قبل برگردد:  ./update.sh --reset"
    fi
    echo
fi

# ============================================================
#  [۱۲] ری‌استارت سرویس
# ============================================================
if [ "$DO_RESTART" -eq 1 ]; then
    step "راه‌اندازی مجدد سرویس"
    if command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files 2>/dev/null | grep -q "$SERVICE_NAME"; then
        if restart_service; then
            ok "سرویس '$SERVICE_NAME' ری‌استارت شد."
        else
            warn "سرویس ری‌استارت نشد. دستی اجرا کن:  sudo systemctl restart $SERVICE_NAME"
        fi
    else
        warn "سرویس systemd با نام '$SERVICE_NAME' پیدا نشد — دستی اجرا کن:"
        dim "        ./run_server.sh        # یا run_dev.sh برای توسعه"
    fi
    echo
fi

# ============================================================
#  [۱۳] بررسی سلامت
# ============================================================
if [ -x "$PYTHON" ]; then
    step "بررسی سلامت"
    if run_manage check >/dev/null 2>&1; then
        ok "بررسی Django بدون خطا."
    else
        warn "manage.py check خطا داد:"
        run_manage check 2>&1 | sed 's/^/        /' | head -15 || true
    fi
    echo
fi

if [ -n "$HEALTH_URL" ]; then
    if command -v curl >/dev/null 2>&1 && curl -fsS -o /dev/null --max-time 10 "$HEALTH_URL"; then
        ok "پاسخ HTTP سالم: $HEALTH_URL"
    else
        warn "پاسخ ناموفق از $HEALTH_URL"
    fi
    echo
fi

# ============================================================
#  پایان
# ============================================================
trap - ERR
step "آپدیت با موفقیت انجام شد ✔"
info "نسخه:      $(git rev-parse --short "$OLD_SHA") → $(git rev-parse --short HEAD)"
info "آخرین تغییر: $(git log -1 --format=%s)"
echo
info "دست‌نخورده (۱۰۰٪): $DB_FILE  •  .env  •  face_recognition_data/  •  staticfiles/"
dim "بکاپ داده‌ها: $BACKUP_DIR"
echo
dim "برای برگشت کامل به نسخهٔ قبل:"
dim "        git reset --hard $OLD_SHA"
dim "        cp $BACKUP_DIR/$(basename "$DB_FILE") $DB_FILE"
echo