"""ساخت دادهٔ داشبورد پنل ادمین — همهٔ آمار با چند کوئری جمع‌بندی‌شده.

نکتهٔ سرعت: تمام شمارش‌ها با `aggregate`/`values().annotate()` انجام می‌شوند
و هیچ حلقهٔ کوئری‌ای (N+1) وجود ندارد. فهرست‌ها هم با `select_related`
گرفته می‌شوند. جدول‌ها ایندکس دارند (مایگریشن `0019_*`).
"""
import datetime as dt

from django.contrib.admin.models import LogEntry
from django.db.models import Count, Max
from django.utils import timezone

from recognition.reports import clock, fa_digits, jalali_full
from users.models import (
    AttendanceSnapshot,
    Break,
    CheckOutError,
    Present,
    Time,
)
from errands.models import Errand

from .diagnostics import fast_checks

IMAGE_EXT = ('.jpg', '.jpeg', '.png')


def _fa(value):
    return fa_digits(value)


def _today():
    return timezone.localdate()


def kpis(today):
    """نشانگرهای اصلی بالای داشبورد."""
    from django.contrib.auth.models import User

    now = timezone.now()
    active_users = User.objects.filter(is_active=True).count()

    present_ids = set(
        Time.objects.filter(date=today, out=False)
        .values_list('user_id', flat=True).distinct()
    )
    present = len(present_ids)
    absent = max(active_users - present, 0)

    on_break = Break.objects.filter(
        break_out__isnull=False, break_in__isnull=True, date=today,
    ).count()

    on_errand = Errand.objects.filter(
        status=Errand.STATUS_APPROVED, start_time__lte=now, end_time__gt=now,
    ).count()

    pending_errands = Errand.objects.filter(status=Errand.STATUS_PENDING).count()
    pending_errors = CheckOutError.objects.filter(
        status=CheckOutError.STATUS_PENDING).count()

    in_today = Time.objects.filter(date=today, out=False).count()
    out_today = Time.objects.filter(date=today, out=True).count()
    snapshots_today = AttendanceSnapshot.objects.filter(
        created_at__date=today).count()

    return [
        {'key': 'employees', 'label': 'کارمندان فعال', 'value': active_users,
         'icon': 'fas fa-users', 'tone': 'primary',
         'url': '/admin/auth/user/'},
        {'key': 'present', 'label': 'حاضر امروز', 'value': present,
         'icon': 'fas fa-user-check', 'tone': 'success',
         'url': '/admin/users/present/?date__exact=%s' % today},
        {'key': 'absent', 'label': 'غایب امروز', 'value': absent,
         'icon': 'fas fa-user-slash', 'tone': 'danger',
         'url': '/admin/users/present/?date__exact=%s' % today},
        {'key': 'break', 'label': 'در استراحت', 'value': on_break,
         'icon': 'fas fa-mug-hot', 'tone': 'warning',
         'url': '/admin/users/break/?date__exact=%s' % today},
        {'key': 'errand', 'label': 'در مأموریت', 'value': on_errand,
         'icon': 'fas fa-route', 'tone': 'info',
         'url': '/admin/errands/errand/?status__exact=approved'},
        {'key': 'in', 'label': 'ورودهای امروز', 'value': in_today,
         'icon': 'fas fa-sign-in-alt', 'tone': 'success',
         'url': '/admin/users/time/?date__exact=%s&out__exact=0' % today},
        {'key': 'out', 'label': 'خروج‌های امروز', 'value': out_today,
         'icon': 'fas fa-sign-out-alt', 'tone': 'secondary',
         'url': '/admin/users/time/?date__exact=%s&out__exact=1' % today},
        {'key': 'errors', 'label': 'خطاهای خروج در انتظار', 'value': pending_errors,
         'icon': 'fas fa-exclamation-triangle', 'tone': 'danger',
         'url': '/admin/users/checkouterror/?status__exact=pending',
         'alert': pending_errors > 0},
        {'key': 'errand_pending', 'label': 'مأموریت در انتظار', 'value': pending_errands,
         'icon': 'fas fa-hourglass-half', 'tone': 'warning',
         'url': '/admin/errands/errand/?status__exact=pending',
         'alert': pending_errands > 0},
        {'key': 'snapshots', 'label': 'عکس لحظهٔ امروز', 'value': snapshots_today,
         'icon': 'fas fa-camera', 'tone': 'secondary',
         'url': '/manage/snapshots/'},
    ]


def attendance_chart(days=14):
    """نمودار حضور `days` روز اخیر (یک کوئری)."""
    today = _today()
    start = today - dt.timedelta(days=days - 1)
    rows = (
        Present.objects.filter(present=True, date__gte=start)
        .values('date')
        .annotate(n=Count('id'))
        .order_by('date')
    )
    by_date = {r['date']: r['n'] for r in rows}
    # در روزهایی که ردیف Present ساخته نشده، خودِ Time مرجع است
    in_rows = (
        Time.objects.filter(out=False, date__gte=start)
        .values('date')
        .annotate(n=Count('user_id', distinct=True))
    )
    for r in in_rows:
        by_date[r['date']] = max(by_date.get(r['date'], 0), r['n'])

    peak = max(list(by_date.values()) or [0])
    peak = max(peak, 1)
    out = []
    for offset in range(days):
        day = start + dt.timedelta(days=offset)
        value = by_date.get(day, 0)
        out.append({
            'date': day,
            'label': jalali_full(day),
            'short': fa_digits(day.strftime('%d')),
            'dow': fa_digits(['د', 'س', 'چ', 'پ', 'ج', 'ش', 'ی'][day.weekday()]),
            'value': value,
            'pct': round(value * 100 / peak),
        })
    return {'days': out, 'peak': peak}


def recent_time(limit=10):
    return list(
        Time.objects.select_related('user', 'exit_reason')
        .order_by('-date', '-time')[:limit]
    )


def pending_checkout_errors(limit=6):
    return list(
        CheckOutError.objects.select_related('user')
        .filter(status=CheckOutError.STATUS_PENDING)
        .order_by('-date')[:limit]
    )


def pending_errands(limit=6):
    return list(
        Errand.objects.select_related('user')
        .filter(status=Errand.STATUS_PENDING)
        .order_by('-start_time')[:limit]
    )


def recent_snapshots(limit=8):
    return list(
        AttendanceSnapshot.objects.select_related('user')
        .order_by('-created_at', '-id')[:limit]
    )


def recent_log(limit=8):
    return list(
        LogEntry.objects.select_related('user')
        .order_by('-action_time')[:limit]
    )


def model_counts():
    """تعداد ردیف هر جدول برای داشبورد (۷ کوئری سادهٔ COUNT)."""
    from django.contrib.auth.models import User
    return {
        'users': _fa(User.objects.count()),
        'time': _fa(Time.objects.count()),
        'present': _fa(Present.objects.count()),
        'break': _fa(Break.objects.count()),
        'errors': _fa(CheckOutError.objects.count()),
        'errands': _fa(Errand.objects.count()),
        'snapshots': _fa(AttendanceSnapshot.objects.count()),
    }


def build_dashboard(request):
    """کل دادهٔ داشبورد را می‌سازد."""
    today = _today()
    return {
        'today': jalali_full(today),
        'dow': ['دوشنبه', 'سه‌شنبه', 'چهارشنبه', 'پنج‌شنبه', 'جمعه',
                'شنبه', 'یکشنبه'][today.weekday()],
        'now_clock': clock(timezone.now()),
        'kpis': kpis(today),
        'chart': attendance_chart(14),
        'recent_time': recent_time(),
        'pending_errors': pending_checkout_errors(),
        'pending_errands': pending_errands(),
        'snapshots': recent_snapshots(),
        'log': recent_log(),
        'counts': model_counts(),
        'checks': fast_checks(),
        'max_time': Time.objects.aggregate(m=Max('time'))['m'],
    }
