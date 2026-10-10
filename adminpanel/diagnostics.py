"""تشخیص خطا و تعمیر یک‌کلیکی داده‌ها و فایل‌های سامانه.

هر بررسی یک «آیتم» است: شمارش + شرح + در صورت امکان یک دکمهٔ «رفع».
هیچ‌کدام از بررسی‌ها چیزی را بدون فشردن دکمهٔ رفع تغییر نمی‌دهند.

هدف: مدیر هرگز مجبور نباشد دستی به دیتابیس یا سرور برود؛ اگر خطایی
پیش آمد همین‌جا دیده و همین‌جا درستش می‌کند.
"""
import datetime as dt
import os

from django.conf import settings
from django.contrib.auth.models import User
from django.db import connection, transaction
from django.db.models import Count
from django.utils import timezone

from users.faceid import TRAINING_DIR, ensure_face_uid
from users.models import (
    AttendanceSnapshot,
    Break,
    CheckOutError,
    Present,
    Time,
    UserProfile,
)
from users.snapshots import snapshots_root

FA = str.maketrans('0123456789', '۰۱۲۳۴۵۶۷۸۹')

# پسوند فایل‌های عکس لحظه — در dashboard هم از همین‌جا import می‌شود.
IMAGE_EXT = ('.jpg', '.jpeg', '.png')


def _fa(v):
    return str(v).translate(FA)


def _abs(path):
    """تبدیل مسیر نسبی به مطلق (با توجه به CWD یا BASE_DIR)."""
    if os.path.isabs(path):
        return path
    for base in (settings.BASE_DIR, os.getcwd()):
        candidate = os.path.join(base, path)
        if os.path.isdir(candidate):
            return candidate
    return os.path.join(settings.BASE_DIR, path)


def training_root():
    return _abs(TRAINING_DIR)


# ============================================================
# شمارش تصاویر هر پوشه (یک‌بار اسکن، با کش کوتاه‌مدت)
# ============================================================
_scan_cache = {'at': 0.0, 'data': None}
SCAN_TTL = 30  # ثانیه


def photo_counts(refresh=False):
    """{برچسب پوشه: تعداد عکس} — یک‌بار پوشهٔ آموزش را می‌خواند."""
    now = timezone.now().timestamp()
    if not refresh and _scan_cache['data'] is not None and now - _scan_cache['at'] < SCAN_TTL:
        return _scan_cache['data']
    data = {}
    root = training_root()
    if os.path.isdir(root):
        try:
            for name in os.listdir(root):
                folder = os.path.join(root, name)
                if not os.path.isdir(folder):
                    continue
                try:
                    n = len([f for f in os.listdir(folder)
                             if f.lower().endswith(('.jpg', '.jpeg', '.png'))])
                except OSError:
                    n = 0
                data[name] = n
        except OSError:
            pass
    _scan_cache['at'] = now
    _scan_cache['data'] = data
    return data


def photo_count_for_user(user):
    """تعداد عکس یک کاربر (با نگاشت face_uid و نام کاربری قدیمی)."""
    counts = photo_counts()
    try:
        uid = user.profile.face_uid
    except UserProfile.DoesNotExist:
        uid = ''
    if uid and counts.get(uid):
        return counts[uid]
    if counts.get(user.username):
        return counts[user.username]
    return 0


def invalidate_photo_cache():
    _scan_cache['data'] = None


# ============================================================
# بررسی‌های دیتابیس (سریع — در داشبورد هم اجرا می‌شوند)
# ============================================================
def check_missing_profiles():
    n = User.objects.filter(profile__isnull=True).count()
    return n, 'کاربرانی هستند که ردیف «نقش کاربر» (UserProfile) ندارند.'


def fix_missing_profiles():
    created = 0
    for user in User.objects.filter(profile__isnull=True).iterator():
        UserProfile.objects.get_or_create(user=user)
        created += 1
    return created, 'پروفایل ساخته شد.'


def check_empty_face_uid():
    n = UserProfile.objects.filter(face_uid='').count()
    return n, 'پروفایل‌هایی که شناسهٔ دائمی چهره ندارند و مدل نمی‌تواند عکس‌هایشان را پیدا کند.'


def fix_empty_face_uid():
    fixed = 0
    for profile in UserProfile.objects.filter(face_uid='').select_related('user'):
        ensure_face_uid(profile.user)
        fixed += 1
    return fixed, 'شناسهٔ چهره ساخته شد.'


def check_duplicate_face_uid():
    n = (UserProfile.objects.exclude(face_uid='')
         .values('face_uid').annotate(c=Count('id')).filter(c__gt=1)
         .count())
    return n, 'چند کاربر یک شناسهٔ چهرهٔ مشترک دارند (عکس‌ها به‌هم می‌ریزد).'


def fix_duplicate_face_uid():
    fixed = 0
    dupes = (UserProfile.objects.exclude(face_uid='')
             .values('face_uid').annotate(c=Count('id')).filter(c__gt=1)
             .values_list('face_uid', flat=True))
    for uid in list(dupes):
        profiles = list(UserProfile.objects.filter(face_uid=uid)
                        .order_by('id'))
        for profile in profiles[1:]:
            for _ in range(20):
                candidate = 'u' + os.urandom(6).hex()
                if not UserProfile.objects.filter(face_uid=candidate).exists():
                    break
            profile.face_uid = candidate
            profile.save(update_fields=['face_uid'])
            fixed += 1
    return fixed, 'شناسهٔ تکراری جدا شد (پوشه‌های عکس باید دستی بررسی شوند).'


def check_present_mismatch():
    """روزهایی که رکورد ورود دارند ولی ردیف «حضور روزانه» ندارند یا برعکس."""
    with_time = set(
        Time.objects.filter(out=False)
        .values_list('user_id', 'date').distinct()
    )
    marked = set(
        Present.objects.filter(present=True)
        .values_list('user_id', 'date')
    )
    missing = len(with_time - marked)
    wrong = len(marked - with_time)
    return missing + wrong, (
        '%s روز بدون ردیف حضور، %s رکورد حضورِ بدون پشتیبان ورود.'
        % (_fa(missing), _fa(wrong))
    )


def fix_present_mismatch():
    """ردیف‌های حضور روزانه را از روی رکوردهای ورود/خروج می‌سازد."""
    return _rebuild_present()


def _rebuild_present():
    pairs = set(
        Time.objects.filter(out=False).values_list('user_id', 'date').distinct()
    )
    to_delete = []
    to_create = []
    for present in Present.objects.all().iterator():
        key = (present.user_id, present.date)
        should = key in pairs
        if present.present != should:
            present.present = should
            present.save(update_fields=['present'])
    for user_id, date in pairs:
        if not Present.objects.filter(user_id=user_id, date=date).exists():
            to_create.append(Present(user_id=user_id, date=date, present=True))
    if to_create:
        Present.objects.bulk_create(to_create, batch_size=500)
    return len(pairs), 'حضور روزانه از روی رکوردهای ورود بازسازی شد.'


def check_duplicate_times():
    """رکوردهای کاملاً یکسان (کاربر/تاریخ/زمان/نوع)."""
    rows = (Time.objects
            .values('user_id', 'date', 'time', 'out')
            .annotate(c=Count('id'))
            .filter(c__gt=1))
    n = sum(r['c'] - 1 for r in rows)
    return n, 'رکورد ورود/خروج تکراری وجود دارد (ساعت کار را دوبار حساب می‌کند).'


def fix_duplicate_times():
    removed = 0
    rows = (Time.objects
            .values('user_id', 'date', 'time', 'out')
            .annotate(c=Count('id'))
            .filter(c__gt=1))
    for row in rows:
        ids = list(Time.objects.filter(
            user_id=row['user_id'], date=row['date'],
            time=row['time'], out=row['out'],
        ).order_by('id').values_list('id', flat=True))
        for pk in ids[1:]:
            Time.objects.filter(pk=pk).delete()
            removed += 1
    return removed, 'رکوردهای تکراری حذف شدند.'


def check_open_breaks():
    today = timezone.localdate()
    n = Break.objects.filter(break_out__isnull=False, break_in__isnull=True,
                             date__lt=today).count()
    return n, 'استراحت‌هایی هستند که هرگز «پایان استراحت» نخورده‌اند و ساعت کار را خراب می‌کنند.'


def fix_open_breaks(minutes=30):
    """استراحت‌های بازِ گذشته را با یک مدت پیش‌فرض می‌بندد."""
    today = timezone.localdate()
    closed = 0
    for br in Break.objects.filter(break_out__isnull=False,
                                   break_in__isnull=True,
                                   date__lt=today):
        br.break_in = br.break_out + dt.timedelta(minutes=minutes)
        if br.break_in > timezone.now():
            br.break_in = timezone.now()
        br.save(update_fields=['break_in'])
        closed += 1
    return closed, 'استراحت‌های باز بسته شدند (مدت پیش‌فرض %s دقیقه).' % _fa(minutes)


def check_future_times():
    limit = timezone.now() + dt.timedelta(days=1)
    n = Time.objects.filter(time__gt=limit).count()
    return n, 'رکورد ورود/خروج با تاریخ در آینده (خطای ساعت سیستم یا ثبت اشتباه).'


def fix_future_times():
    limit = timezone.now() + dt.timedelta(days=1)
    ids = list(Time.objects.filter(time__gt=limit).values_list('id', flat=True))
    Time.objects.filter(id__in=ids).delete()
    return len(ids), 'رکوردهای زمان آینده حذف شدند.'


def check_pending_checkout_errors():
    n = CheckOutError.objects.filter(status=CheckOutError.STATUS_PENDING).count()
    return n, 'خروج خودکارِ پایان شب که هنوز اصلاح نشده است.'


def fix_pending_checkout_errors():
    updated = CheckOutError.objects.filter(
        status=CheckOutError.STATUS_PENDING).update(
        status=CheckOutError.STATUS_RESOLVED,
        resolved_by=None,
        resolved_at=timezone.now(),
    )
    return updated, 'همهٔ خطاهای خروج به‌عنوان رفع‌شده علامت خوردند.'


def check_db_integrity():
    try:
        with connection.cursor() as cursor:
            cursor.execute('PRAGMA integrity_check')
            row = cursor.fetchone()
        ok = row and str(row[0]).lower() == 'ok'
        return (0 if ok else 1,
                'فایل دیتابیس سالم است.' if ok else
                'دیتابیس خراب گزارش شده؛ از یک نسخهٔ پشتیبان بازیابی کنید.')
    except Exception as exc:  # pragma: no cover - دیتابیس غیر SQLite
        return 1, 'بررسی یکپارچگی انجام نشد: %s' % exc


def check_stale_sessions():
    from django.contrib.sessions.models import Session
    n = Session.objects.filter(expire_date__lt=timezone.now()).count()
    return n, 'نشست‌های منقضی‌ای که هنوز در جدول نشست‌ها مانده‌اند.'


def fix_stale_sessions():
    from django.contrib.sessions.models import Session
    n = Session.objects.filter(expire_date__lt=timezone.now()).delete()[0]
    return n, 'نشست‌های منقضی پاک شدند.'


def check_log_size():
    path = os.path.join(settings.BASE_DIR, 'django.log')
    if not os.path.isfile(path):
        return 0, 'فایل لاگ وجود ندارد (هنوز خطایی ثبت نشده).'
    size = os.path.getsize(path)
    mb = size / (1024 * 1024)
    over = mb > 5
    return (1 if over else 0,
            'حجم لاگ: %s مگابایت.%s' % (
                _fa('%.1f' % mb),
                ' بهتر است پاک شود.' if over else ''))


def fix_log_size():
    path = os.path.join(settings.BASE_DIR, 'django.log')
    if os.path.isfile(path):
        open(path, 'w').close()
        return 1, 'فایل لاگ خالی شد.'
    return 0, 'فایل لاگ وجود نداشت.'


# ============================================================
# بررسی‌های فایلی (کمی سنگین‌تر — فقط در صفحهٔ سلامت)
# ============================================================
def check_snapshot_missing_files():
    missing = 0
    root = os.path.realpath(snapshots_root())
    for snap in AttendanceSnapshot.objects.all().only('id', 'photo'):
        p = os.path.realpath(os.path.join(root, snap.photo))
        if not (p.startswith(root + os.sep) and os.path.isfile(p)):
            missing += 1
    return missing, 'رکورد عکس لحظه‌ای هست که فایلش پیدا نمی‌شود.'


def fix_snapshot_missing_files():
    removed = 0
    root = os.path.realpath(snapshots_root())
    for snap in AttendanceSnapshot.objects.all().only('id', 'photo'):
        p = os.path.realpath(os.path.join(root, snap.photo))
        if not (p.startswith(root + os.sep) and os.path.isfile(p)):
            snap.delete()
            removed += 1
    return removed, 'رکوردهای بدون فایل حذف شدند.'


def check_orphan_snapshot_files():
    """فایل‌هایی که در دیتابیس ردیفی ندارند."""
    root = snapshots_root()
    if not os.path.isdir(root):
        return 0, 'پوشهٔ عکس‌های لحظه وجود ندارد.'
    known = set(AttendanceSnapshot.objects.values_list('photo', flat=True))
    orphans = []
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if not name.lower().endswith(IMAGE_EXT):
                continue
            rel = os.path.relpath(os.path.join(dirpath, name), root)
            if rel not in known:
                orphans.append(rel)
    return len(orphans), 'فایل عکس بدون رکورد دیتابیس (فضای بیهوده).'


def fix_orphan_snapshot_files():
    root = snapshots_root()
    if not os.path.isdir(root):
        return 0, 'پوشه‌ای برای پاک‌سازی نبود.'
    known = set(AttendanceSnapshot.objects.values_list('photo', flat=True))
    removed = 0
    root_real = os.path.realpath(root)
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if not name.lower().endswith(IMAGE_EXT):
                continue
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root)
            if rel in known:
                continue
            p = os.path.realpath(full)
            if p.startswith(root_real + os.sep) and os.path.isfile(p):
                try:
                    os.remove(p)
                    removed += 1
                except OSError:
                    pass
    invalidate_photo_cache()
    return removed, 'فایل‌های بدون رکورد حذف شدند.'


def check_training_orphans():
    root = training_root()
    if not os.path.isdir(root):
        return 0, 'پوشهٔ دیتاست چهره ساخته نشده (هنوز عکسی آموزش داده نشده).'
    known = set(UserProfile.objects.exclude(face_uid='').values_list('face_uid', flat=True))
    known |= set(User.objects.values_list('username', flat=True))
    orphans = [name for name in os.listdir(root)
               if os.path.isdir(os.path.join(root, name)) and name not in known]
    return len(orphans), 'پوشهٔ عکس بدون کاربر متناظر (دیتاست قدیمی/یتیم).'


def check_users_without_photos():
    counts = photo_counts()
    missing = []
    for user in User.objects.filter(is_active=True).select_related('profile'):
        uid = ''
        try:
            uid = user.profile.face_uid
        except UserProfile.DoesNotExist:
            pass
        if not counts.get(uid or '') and not counts.get(user.username):
            missing.append(user.username)
    return len(missing), (
        'کاربران فعال بدون عکس چهره: %s' %
        (', '.join(missing[:10]) + (' و …' if len(missing) > 10 else '')
         if missing else '—')
    )


def check_static_collected():
    key = os.path.join(settings.STATIC_ROOT, 'recognition', 'css',
                       'bootstrap.rtl.min.css')
    exists = os.path.isfile(key)
    return (0 if exists else 1,
            'فایل‌های استاتیک جمع‌آوری شده‌اند.' if exists else
            'خروجی collectstatic پیدا نشد؛ در حالت تولید استایل‌ها لود نمی‌شوند.')


def fix_static_collected():
    from django.core.management import call_command
    out = []
    call_command('collectstatic', interactive=False, verbosity=0, stdout=out)
    return 1, 'استاتیک‌ها دوباره جمع‌آوری شدند.'


# ============================================================
# ثبت بررسی‌ها
# ============================================================
class Check:
    def __init__(self, key, title, group, level, fn, fix=None,
                 fix_label='', hint='', fast=False):
        self.key = key
        self.title = title
        self.group = group
        self.level = level          # 'error' | 'warn' | 'info'
        self.fn = fn
        self.fix = fix
        self.fix_label = fix_label
        self.hint = hint
        self.fast = fast            # در داشبورد هم اجرا شود

    def run(self):
        try:
            count, detail = self.fn()
            error = None
        except Exception as exc:  # یک بررسی خراب نباید کل صفحه را بکشد
            count, detail, error = 1, 'اجرای بررسی با خطا مواجه شد: %s' % exc, str(exc)
        status = 'ok' if not count else self.level
        return {
            'key': self.key,
            'title': self.title,
            'group': self.group,
            'status': status,
            'count': count,
            'count_fa': _fa(count),
            'detail': detail,
            'fixable': bool(self.fix),
            'fix_label': self.fix_label or 'رفع مشکل',
            'hint': self.hint,
            'error': error,
        }


CHECKS = [
    Check('integrity', 'یکپارچگی فایل دیتابیس', 'پایگاه داده', 'error',
          check_db_integrity, fast=True),
    Check('missing_profiles', 'پروفایل نقش ساخته نشده', 'پایگاه داده', 'error',
          check_missing_profiles, fix_missing_profiles,
          'ساخت پروفایل‌ها', fast=True),
    Check('empty_face_uid', 'شناسهٔ چهره خالی', 'چهره', 'error',
          check_empty_face_uid, fix_empty_face_uid,
          'ساخت شناسه‌ها', fast=True),
    Check('duplicate_face_uid', 'شناسهٔ چهرهٔ تکراری', 'چهره', 'error',
          check_duplicate_face_uid, fix_duplicate_face_uid,
          'جداسازی شناسه‌ها', fast=True),
    Check('present_mismatch', 'ناهماهنگی حضور روزانه', 'پایگاه داده', 'error',
          check_present_mismatch, fix_present_mismatch,
          'بازسازی از روی رکوردها', fast=True),
    Check('duplicate_times', 'رکورد ورود/خروج تکراری', 'پایگاه داده', 'error',
          check_duplicate_times, fix_duplicate_times,
          'حذف تکراری‌ها', fast=True),
    Check('open_breaks', 'استراحتِ بازِ تمام‌نشده', 'پایگاه داده', 'warn',
          check_open_breaks, fix_open_breaks,
          'بستن استراحت‌ها', fast=True),
    Check('future_times', 'رکورد زمان در آینده', 'پایگاه داده', 'warn',
          check_future_times, fix_future_times,
          'حذف رکوردهای آینده', fast=True),
    Check('pending_errors', 'خطای خروج در انتظار بررسی', 'صف کاری', 'warn',
          check_pending_checkout_errors, fix_pending_checkout_errors,
          'علامت‌گذاری همه به‌عنوان رفع‌شده', fast=True),
    Check('stale_sessions', 'نشست‌های منقضی', 'سیستم', 'info',
          check_stale_sessions, fix_stale_sessions,
          'پاک‌سازی نشست‌ها', fast=True),
    Check('log_size', 'حجم فایل لاگ', 'سیستم', 'warn',
          check_log_size, fix_log_size, 'خالی کردن لاگ', fast=True),
    Check('snapshot_missing_files', 'رکورد عکس بدون فایل', 'فایل‌ها', 'warn',
          check_snapshot_missing_files, fix_snapshot_missing_files,
          'حذف رکوردهای خراب'),
    Check('orphan_snapshot_files', 'فایل عکس بدون رکورد', 'فایل‌ها', 'info',
          check_orphan_snapshot_files, fix_orphan_snapshot_files,
          'حذف فایل‌های بی‌رکورد'),
    Check('training_orphans', 'پوشهٔ یتیم دیتاست چهره', 'چهره', 'info',
          check_training_orphans, None, ''),
    Check('users_without_photos', 'کارمند بدون عکس چهره', 'چهره', 'warn',
          check_users_without_photos, None, ''),
    Check('static_collected', 'جمع‌آوری فایل‌های استاتیک', 'سیستم', 'error',
          check_static_collected, fix_static_collected,
          'اجرای collectstatic'),
]

GROUP_ORDER = ['پایگاه داده', 'چهره', 'فایل‌ها', 'صف کاری', 'سیستم']


def all_checks():
    """همهٔ بررسی‌ها (شامل بررسی‌های فایلی)."""
    items = [c.run() for c in CHECKS]
    return sorted(items, key=lambda i: (
        GROUP_ORDER.index(i['group']) if i['group'] in GROUP_ORDER else 99,
        {'error': 0, 'warn': 1, 'info': 2, 'ok': 3}.get(i['status'], 9),
    ))


def fast_checks():
    """فقط بررسی‌های دیتابیسیِ سریع برای داشبورد."""
    return [c.run() for c in CHECKS if c.fast]


def fix_check(key, minutes=30):
    """اجرای تعمیر یک بررسی. خطا را به‌صورت (False, پیام) برمی‌گرداند."""
    for check in CHECKS:
        if check.key != key:
            continue
        if check.fix is None:
            return False, 'این مورد قابل رفع خودکار نیست.'
        try:
            with transaction.atomic():
                n, msg = check.fix(minutes) if key == 'open_breaks' else check.fix()
            invalidate_photo_cache()
            return True, '«%s»: %s (%s مورد)' % (check.title, msg, _fa(n))
        except Exception as exc:
            return False, 'خطا هنگام رفع «%s»: %s' % (check.title, exc)
    return False, 'بررسی پیدا نشد.'


def fix_all(minutes=30):
    """رفع همهٔ موارد قابل رفع. پیام‌ها را جمع می‌کند."""
    done, failed = [], []
    for check in CHECKS:
        if check.fix is None:
            continue
        # مواردی که فقط گزارش‌اند (info) دست نمی‌خورند مگر صراحتاً خواسته شود
        ok, msg = fix_check(check.key, minutes=minutes)
        (done if ok else failed).append(msg)
    return done, failed
