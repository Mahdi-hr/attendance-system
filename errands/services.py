"""منطق محاسباتی مأموریت.

این ماژول دو کار انجام می‌دهد که نباید در ویو یا تمپلیت باشد:

۱) **تقسیم بازه به روز** — یک مأموریت می‌تواند از ساعت ۱۷ دیروز تا ۰۴ امروز
   طول بکشد. برای اینکه در گزارش «روزبه‌روز» درست بنشیند، باید به دو تکهٔ
   روزانه شکسته شود.

۲) **کسر همپوشانی** — اگر کارمند ساعت ۱۰ تا ۱۲ در محل کار بوده (ورود/خروج
   فیزیکی) و مأموریت تأییدشده‌اش ۸ تا ۱۶ بوده، آن ۲ ساعتِ مشترک نباید
   دوبار شمرده شود. مأموریت «مؤثر» = بازهٔ مأموریت منهای بازه‌های حضور.

قاعدهٔ طلایی: اجتماعِ (حضور ∪ مأموریت) = ساعت کار روز.
"""

import datetime as dt
from collections import defaultdict

from django.utils import timezone

from .models import Errand


# ============================================================
#  کمک‌کننده‌های تاریخ/زمان
# ============================================================

def local_midnight(date):
    """نیمه‌شب به وقت تهران برای یک تاریخ میلادی (aware)."""
    naive = dt.datetime.combine(date, dt.time.min)
    return timezone.make_aware(naive, timezone.get_current_timezone())


def split_interval_by_day(start, end):
    """یک بازهٔ زمانی را به تکه‌های روزانه می‌شکند.

    ورودی : دو datetime آگاهانه از هم (start < end)
    خروجی: لیستی از (date, seg_start, seg_end) — به ترتیب زمانی

    مثال: ۱۷:۰۰ دیروز → ۰۴:۰۰ امروز
        → [(دیروز, 17:00, 24:00), (امروز, 00:00, 04:00)]
    """
    if not start or not end or end <= start:
        return []

    segments = []
    cursor = start
    # سقف تعداد حلقه: یک بازهٔ ۴۰۰ روزه حداکثر ۴۰۰ تکه می‌دهد.
    for _ in range(400):
        if cursor >= end:
            break
        local = timezone.localtime(cursor)
        day_end = local_midnight(local.date() + dt.timedelta(days=1))
        seg_end = min(end, day_end)
        if seg_end > cursor:
            segments.append((local.date(), cursor, seg_end))
        cursor = seg_end
    return segments


def merge_intervals(intervals):
    """ادغام بازه‌های همپوشان یا چسبیده، و مرتب‌سازی.

    تضمین می‌کند مدت دو بار شمرده نشود وقتی چند مأموریتِ تأییدشده
    روی هم افتاده‌اند.
    """
    if not intervals:
        return []
    ordered = sorted(intervals, key=lambda iv: iv[0])
    merged = [list(ordered[0])]
    for start, end in ordered[1:]:
        last = merged[-1]
        # چسبیده هم ادغام می‌شود (end == start) تا شکاف صفر بماند
        if start <= last[1]:
            if end > last[1]:
                last[1] = end
        else:
            merged.append([start, end])
    return [(a, b) for a, b in merged]


def subtract_intervals(base, cuts):
    """از بازه‌های `base` قسمت‌هایی را که با `cuts` تلاقی دارند حذف می‌کند.

    base : لیستی از (start, end)
    cuts : لیستی از (start, end)

    خروجی: بازه‌های باقیمانده — هیچ قسمتی با cuts همپوشانی ندارد.
    """
    if not base:
        return []
    if not cuts:
        return list(base)

    cuts = merge_intervals(cuts)
    result = []
    for b_start, b_end in merge_intervals(base):
        cursor = b_start
        for c_start, c_end in cuts:
            if c_end <= cursor:
                continue
            if c_start >= b_end:
                break
            if c_start > cursor:
                result.append((cursor, min(c_start, b_end)))
            cursor = max(cursor, c_end)
            if cursor >= b_end:
                break
        if cursor < b_end:
            result.append((cursor, b_end))
    return [(a, b) for a, b in result if b > a]


def total_seconds(intervals):
    """مجموع ثانیه‌های یک لیست بازه."""
    return int(sum((b - a).total_seconds() for a, b in intervals or []))


# ============================================================
#  ساخت نقشهٔ مأموریت برای گزارش‌ها
# ============================================================

def build_raw_map(users, date_from=None, date_to=None, include_pending=False):
    """نقشهٔ بازه‌های خام مأموریت به تفکیک (user_id, date).

    برمی‌گرداند:
        { (user_id, date): {'intervals': [(start, end), ...],
                            'errands':   [Errand, ...] } }

    فقط مأموریت‌های «تأییدشده» وارد محاسبهٔ ساعت کار می‌شوند — این همان
    تصمیمی است که «مأموریت فقط بعد از تأیید ادمین اثر بگذارد» را تضمین
    می‌کند.
    """
    if users is None:
        users = []
    user_ids = [u.pk for u in users]
    if not user_ids:
        return {}

    statuses = [Errand.STATUS_APPROVED]
    if include_pending:
        statuses.append(Errand.STATUS_PENDING)

    qs = (
        Errand.objects
        .filter(user_id__in=user_ids, status__in=statuses)
        .for_window(date_from, date_to)
        .select_related('user')
        .order_by('start_time', 'id')
    )

    bucket = defaultdict(lambda: {'intervals': [], 'errands': []})
    for errand in qs:
        for date, seg_start, seg_end in split_interval_by_day(
            errand.start_time, errand.end_time
        ):
            key = (errand.user_id, date)
            bucket[key]['intervals'].append((seg_start, seg_end))
            bucket[key]['errands'].append(errand)

    # ادغام بازه‌های همپوشان تا مدت دوباره شمرده نشود
    for value in bucket.values():
        value['intervals'] = merge_intervals(value['intervals'])
    return dict(bucket)


def effective_seconds(raw_intervals, work_sessions):
    """مدت مأموریتِ «قابل حساب» برای یک روز.

    raw_intervals : بازه‌های خام مأموریتِ همان روز
    work_sessions : بازه‌های حضور فیزیکیِ همان روز (ورود→خروج)

    مثال:
        حضور 08:00–12:00 ، مأموریت 08:00–16:00
        → مأموریت مؤثر = 12:00–16:00 = ۴ ساعت
        → ساعت کار روز = ۴ (حضور) + ۴ (مأموریت) = ۸ ساعت  ✔ بدون دوباره‌شماری
    """
    return total_seconds(subtract_intervals(raw_intervals, work_sessions))


def open_errand_now(user):
    """مأموریت تأییدشده‌ای که همین الان «در جریان» است، یا None."""
    now = timezone.now()
    return (
        Errand.objects
        .approved()
        .filter(user=user, start_time__lte=now, end_time__gt=now)
        .order_by('-start_time')
        .first()
    )


def pending_count(users=None):
    """تعداد درخواست‌های در انتظار بررسی — برای نشان‌دادن در منو/داشبورد."""
    qs = Errand.objects.pending()
    if users is not None:
        qs = qs.filter(user__in=users)
    return qs.count()


def pending_seconds_for_user(user, date=None):
    """مجموع ثانیهٔ مأموریت‌های در انتظار بررسیِ یک کارمند.

    برای هشدار «اگر تأیید شود، X ساعت به ساعت کارت اضافه می‌شود».
    """
    qs = Errand.objects.pending().filter(user=user)
    if date is not None:
        qs = qs.for_window(date, date)
    intervals = []
    for errand in qs:
        intervals.extend(split_interval_by_day(errand.start_time, errand.end_time))
    return total_seconds(intervals)


def coverage_summary(user, date_from=None, date_to=None):
    """خلاصهٔ مأموریت یک کارمند در یک بازه.

    خروجی دیکشنری با کلیدهای:
        approved_seconds, approved_count, pending_seconds, pending_count,
        total_seconds, total_text
    """
    approved = Errand.objects.approved().filter(user=user).for_window(date_from, date_to)
    pending = Errand.objects.pending().filter(user=user).for_window(date_from, date_to)

    def _seconds(qs):
        intervals = []
        for errand in qs:
            intervals.extend(split_interval_by_day(errand.start_time, errand.end_time))
        return total_seconds(merge_intervals(intervals))

    approved_seconds = _seconds(approved)
    pending_seconds = _seconds(pending)
    total = approved_seconds + pending_seconds

    from recognition.reports import format_hours
    return {
        'approved_seconds': approved_seconds,
        'approved_count': approved.count(),
        'pending_seconds': pending_seconds,
        'pending_count': pending.count(),
        'total_seconds': total,
        'total_text': format_hours(total),
    }