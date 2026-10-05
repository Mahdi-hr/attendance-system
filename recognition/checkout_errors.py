"""خروج خودکار در ساعت ۲۴ برای جلسه‌های باز (ورود بدون خروج).

اگر کاربر تا پایان روز (ساعت ۲۴) خروج ثبت نکرده باشد، به‌جای این‌که سیستم
باگ بخورد، یک خروج سیستمی دقیقاً در ساعت ۲۴:۰۰ (۲۳:۵۹:۵۹ همان روز) ثبت می‌شود
و یک `CheckOutError` ساخته می‌شود تا مدیر بتواند در پنل مدیریت آن را اصلاح کند.
"""
import datetime as _dt
import threading
import time as _time

from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.utils import timezone

from users.models import Time, Present, CheckOutError
from .reports import pair_time_events

# ---------------------------------------------------------------------------
# محدودکردن بسامد اجرا
# ---------------------------------------------------------------------------
# `auto_fix_missing_checkouts` کل جدول Time را اسکن می‌کند. این تابع قبلاً از
# هر درخواست `/api/attendance-status/` صدا زده می‌شد و آن اندپوینت هر ۱ ثانیه به
# ازای هر کاربرِ صفحهٔ استریم پول می‌زد؛ یعنی با دو کاربر هم‌زمان، دو اسکن کامل
# در ثانیه روی دیتابیس — که دقیقاً همان چیزی است که سرور را زیر بار هم‌زمان زمین
# می‌زد. حالا حداکثر یک‌بار در هر بازهٔ `THROTTLE_SEC` اجرا می‌شود.
THROTTLE_SEC = 60.0
_throttle_lock = threading.Lock()
_last_run_at = [0.0]


def auto_fix_missing_checkouts_throttled(min_interval_sec=THROTTLE_SEC):
    """اجرای محدودشدهٔ `auto_fix_missing_checkouts`.

    اگر کمتر از `min_interval_sec` از آخرین اجرا گذشته باشد، بدون کار کردن روی
    دیتابیس `0` برمی‌گرداند. چون فرایند گیت‌ون ساده (GUNICORN_WORKERS=1) است، یک
    متغیر ماژولی برای نگه‌داشتن زمان آخرین اجرا کافی است.
    """
    now = _time.monotonic()
    with _throttle_lock:
        if (now - _last_run_at[0]) < min_interval_sec:
            return 0
        _last_run_at[0] = now
    try:
        return auto_fix_missing_checkouts()
    except Exception as exc:  # هیچ خطایی نباید صفحهٔ وضعیت را خراب کند
        print(f"[WARN] اجرای خودکار خروج گرفتن با خطا مواجه شد: {exc}")
        return 0


def auto_fix_missing_checkouts():
    """بستن تمام جلسه‌های حضوری که بدون خروج مانده‌اند.

    برای هر (کارمند، تاریخ) که روزش کامل گذشته است ولی هنوز جلسه‌ی باز دارد،
    یک خروج سیستمی در ۲۳:۵۹:۵۹ همان تاریخ ثبت می‌کند و یک `CheckOutError`
    می‌سازد تا مدیر آن را بررسی/اصلاح کند.

    Idempotent است: اگر برای آن (کارمند، تاریخ) قبلاً error ساخته شده باشد،
    دوباره چیزی ساخته نمی‌شود. تعداد خطاهای جدید ساخته‌شده را برمی‌گرداند.
    """
    now = timezone.localtime(timezone.now())
    created = 0

    existing = set(CheckOutError.objects.values_list('user_id', 'date'))

    grouped = {}
    qs = (Time.objects
          .filter(time__isnull=False)
          .order_by('user_id', 'date', 'time', 'id'))
    for ev in qs:
        grouped.setdefault((ev.user_id, ev.date), []).append(ev)

    for (user_id, date), events in grouped.items():
        # فقط روزهایی که کاملاً تمام شده‌اند (اکنون بعد از آن روز است).
        # استفاده از تاریخ تهران برای تطابق با منطقه زمانی سرور
        if now.date() <= date:
            continue
        if (user_id, date) in existing:
            continue

        sessions, open_time = pair_time_events(events)
        if open_time is None:
            continue

        auto_time = timezone.make_aware(_dt.datetime.combine(date, _dt.time(23, 59, 59)))
        try:
            with transaction.atomic():
                if CheckOutError.objects.filter(user_id=user_id, date=date).exists():
                    continue
                user = User.objects.get(id=user_id)
                pres, _ = Present.objects.get_or_create(
                    user=user, date=date, defaults={'present': True})
                if not pres.present:
                    pres.present = True
                    pres.save(update_fields=['present'])
                rec = Time.objects.create(
                    user=user, date=date, time=auto_time, out=True)
                CheckOutError.objects.create(
                    user=user, date=date,
                    time_record=rec, auto_out_time=auto_time)
            created += 1
        except IntegrityError:
            # رکورد خطا هم‌زمان توسط جریان دیگری ساخته شد؛ نادیده بگیر.
            continue
        except User.DoesNotExist:
            continue

    return created