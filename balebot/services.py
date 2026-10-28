"""منطق مشترک ربات: دسترسی، وضعیت حضور، گزارش و ساخت مأموریت.

هیچ‌کدام از قوانین کسب‌وکار اینجا دوباره‌نویسی نشده‌اند؛ همه از همان
ماژول‌های سایت استفاده می‌کنند (`errands.forms`، `recognition.reports`،
`users.roles`) تا رفتار ربات و سایت یکسان بماند.
"""

import datetime as dt
import itertools

import jdatetime
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.utils import timezone

from errands.forms import ErrandForm
from errands.models import Errand
from errands.services import open_errand_now
from recognition.forms import JalaliDateTimePickerField
from recognition.reports import (
    clock,
    fa_digits,
    format_hours,
    jalali_full,
    pair_time_events,
)
from users.models import Break, CheckOutError, Present, Time
from users.roles import (
    ROLE_ADMIN,
    ROLE_BOTH,
    ROLE_USER,
    get_role,
    is_admin_user,
)

from .client import BaleError
from .models import BaleAccount

User = get_user_model()

MONTH_FA = jdatetime.date.j_months_fa


# ============================================================
# نقش و دسترسی
# ============================================================

def is_admin_mode(account):
    """آیا این حساب در همین لحظه مجاز به کارهای مدیریتی است؟"""
    user = account.django_user
    if not user or not user.is_active:
        return False
    if user.is_superuser:
        return True
    role = get_role(user)
    if role == ROLE_ADMIN:
        return True
    if role == ROLE_BOTH:
        return account.active_role == ROLE_ADMIN
    return False


def can_switch_role(account):
    """فقط کاربر «both» (و نه سوپرادمین) دکمهٔ تغییر نقش دارد."""
    user = account.django_user
    if not user or not user.is_active or user.is_superuser:
        return False
    return get_role(user) == ROLE_BOTH


def role_title(account):
    return 'مدیر' if is_admin_mode(account) else 'کاربر'


def user_label(user):
    full = ('%s %s' % (user.first_name, user.last_name)).strip()
    username = user.get_username()
    if full:
        return '%s (@%s)' % (full, username)
    return '@%s' % username


# ============================================================
# ارسال پیام
# ============================================================

def safe_send(client, account, text, reply_markup=None):
    """ارسال پیام با مدیریت خطا؛ اگر کاربر ربات را بلاک کرده باشد علامت می‌زند."""
    try:
        return client.send_message(
            account.chat_id or account.bale_user_id, text,
            reply_markup=reply_markup,
        )
    except BaleError as exc:
        if getattr(exc, 'code', None) == 403:
            account.is_blocked = True
            account.save(update_fields=['is_blocked'])
        return None


def notify_user(client, user, text):
    """پیام به همهٔ حساب‌های بلهٔ یک کاربر سامانه."""
    for account in BaleAccount.objects.filter(
        django_user=user, is_blocked=False,
    ):
        safe_send(client, account, text)


def notify_admins(client, text, exclude_account=None):
    """پیام به همهٔ حساب‌هایی که کاربرشان ادمین است (صرف‌نظر از نقش فعال)."""
    qs = (
        BaleAccount.objects
        .filter(is_blocked=False, django_user__isnull=False)
        .select_related('django_user')
    )
    for account in qs:
        if exclude_account is not None and account.pk == exclude_account.pk:
            continue
        if is_admin_user(account.django_user):
            safe_send(client, account, text)


# ============================================================
# تاریخ و اعداد
# ============================================================

def today_jalali():
    return jalali_full(timezone.localdate())


def dt_label(value):
    """«شنبه ۲ آبان ۱۴۰۵ — ۰۹:۳۰»"""
    if not value:
        return '—'
    local = timezone.localtime(value)
    return '%s — %s' % (jalali_full(local.date()), fa_digits(clock(value)))


def to_jalali_string(value):
    """datetime آگاه → رشتهٔ شمسی مورد انتظار فرم («۱۴۰۵/۰۸/۰۲ ۰۹:۰۰»)."""
    local = timezone.localtime(value) if timezone.is_aware(value) else value
    return jdatetime.datetime.fromgregorian(datetime=local).strftime('%Y/%m/%d %H:%M')


def parse_jalali_datetime(text):
    """رشتهٔ شمسی کاربر → (datetime, خطا). از همان پارسر فرم سایت استفاده می‌کند."""
    field = JalaliDateTimePickerField()
    try:
        return field.clean(text), None
    except ValidationError as exc:
        return None, exc.messages[0]


def month_bounds():
    """(اول ماه شمسی جاری، امروز، عنوان مثل «آبان ۱۴۰۵»)"""
    today = timezone.localdate()
    j = jdatetime.date.fromgregorian(date=today)
    start = jdatetime.date(j.year, j.month, 1).togregorian()
    return start, today, '%s %s' % (MONTH_FA[j.month - 1], fa_digits(j.year))


# ============================================================
# خلاصه‌ها
# ============================================================

def _worked_seconds(events):
    sessions, open_time = pair_time_events(events)
    total = sum((end - start).total_seconds() for start, end in sessions)
    if open_time:
        total += (timezone.now() - open_time).total_seconds()
    return int(total)


def today_status_text(user):
    today = timezone.localdate()
    present = Present.objects.filter(user=user, date=today).first()
    events = list(Time.objects.filter(user=user, date=today).order_by('time'))
    sessions, open_time = pair_time_events(events)

    first_in = next((ev.time for ev in events if not ev.out), None)
    last_out = next((ev.time for ev in reversed(events) if ev.out), None)

    open_break = (
        Break.objects
        .filter(user=user, date=today, break_out__isnull=False, break_in__isnull=True)
        .order_by('-break_out')
        .first()
    )
    errand = open_errand_now(user)
    pending = Errand.objects.pending().filter(user=user).count()

    lines = [
        '📊 وضعیت امروز',
        '📅 %s' % today_jalali(),
        '',
        '✅ حضور روزانه: %s' % ('ثبت شده' if present and present.present else 'ثبت نشده'),
        '🟢 اولین ورود: %s' % (fa_digits(clock(first_in)) if first_in else '—'),
        '🔴 آخرین خروج: %s' % (fa_digits(clock(last_out)) if last_out else '—'),
        '⏱ کارکرد امروز: %s' % format_hours(_worked_seconds(events)),
    ]
    if open_time:
        lines.append('🟡 همین الان در حال حضور هستید (از %s).' % fa_digits(clock(open_time)))
    if open_break:
        lines.append('☕ استراحت باز: از %s' % fa_digits(clock(open_break.break_out)))
    if errand:
        lines.append('🚗 مأموریت فعال: %s' % (errand.destination or errand.reason))
        lines.append('   تا %s' % dt_label(errand.end_time))
    if pending:
        lines.append('⏳ مأموریت در انتظار تأیید: %s مورد' % fa_digits(pending))

    if not events and not present:
        lines.append('')
        lines.append('ℹ️ هنوز ورودی برای امروز ثبت نشده است.')
    return '\n'.join(lines)


def month_summary_text(user):
    start, today, label = month_bounds()

    present_days = Present.objects.filter(
        user=user, date__gte=start, date__lte=today, present=True,
    ).count()

    rows = (
        Time.objects
        .filter(user=user, date__gte=start, date__lte=today)
        .order_by('date', 'time')
    )
    total_seconds = 0
    for _date, events in itertools.groupby(rows, key=lambda ev: ev.date):
        total_seconds += _worked_seconds(list(events))

    approved = Errand.objects.approved().filter(user=user).for_window(start, today)
    approved_seconds = sum(e.duration_seconds for e in approved)

    pending = Errand.objects.pending().filter(user=user).for_window(start, today)

    return '\n'.join([
        '📈 گزارش ماه — %s' % label,
        '',
        '🗓 روزهای حضور: %s' % fa_digits(present_days),
        '⏱ مجموع کارکرد: %s' % format_hours(total_seconds),
        '🚗 مأموریت تأییدشده: %s مورد — %s' % (
            fa_digits(approved.count()), format_hours(approved_seconds),
        ),
        '⏳ مأموریت در انتظار: %s مورد' % fa_digits(pending.count()),
    ])


def admin_today_text():
    today = timezone.localdate()
    active_users = User.objects.filter(is_active=True).count()
    present_users = (
        Present.objects.filter(date=today, present=True)
        .values('user_id').distinct().count()
    )

    rows = (
        Time.objects.filter(date=today)
        .select_related('user')
        .order_by('user__username', 'time')
    )
    open_sessions = []
    for user, events in itertools.groupby(rows, key=lambda ev: ev.user):
        _sessions, open_time = pair_time_events(list(events))
        if open_time:
            open_sessions.append((user, open_time))

    pending_errors = CheckOutError.objects.filter(
        status=CheckOutError.STATUS_PENDING,
    ).count()

    lines = [
        '📊 داشبورد امروز',
        '📅 %s' % today_jalali(),
        '',
        '👥 کاربران فعال: %s' % fa_digits(active_users),
        '✅ حاضر: %s' % fa_digits(present_users),
        '🟡 بدون خروج (در حال حضور): %s' % fa_digits(len(open_sessions)),
        '⚠️ خطای خروج در انتظار: %s' % fa_digits(pending_errors),
    ]
    if open_sessions:
        lines.append('')
        lines.append('در حال حضور:')
        for user, open_time in open_sessions[:12]:
            lines.append('• %s — از %s' % (user_label(user), fa_digits(clock(open_time))))
        if len(open_sessions) > 12:
            lines.append('… و %s نفر دیگر' % fa_digits(len(open_sessions) - 12))
    return '\n'.join(lines)


# ============================================================
# مأموریت
# ============================================================

def create_errand(user, payload):
    """ساخت مأموریت با همان اعتبارسنجی فرم سایت.

    خروجی: (errand, خطا) — یکی از دو مقدار None است.
    """
    data = {
        'start': payload.get('start', ''),
        'end': payload.get('end', ''),
        'reason': payload.get('reason', ''),
        'destination': payload.get('destination', ''),
        'contact': payload.get('contact', ''),
    }
    form = ErrandForm(data=data, user=user)
    if not form.is_valid():
        for _field, errors in form.errors.items():
            return None, errors[0]
        return None, 'اطلاعات واردشده معتبر نیست.'

    errand = form.save(commit=False)
    errand.user = user
    errand.save()
    return errand, None


def errand_status_badge(errand):
    mapping = {
        Errand.STATUS_PENDING: '⏳ در انتظار',
        Errand.STATUS_APPROVED: '✅ تأییدشده',
        Errand.STATUS_REJECTED: '❌ ردشده',
        Errand.STATUS_CANCELLED: '🚫 لغوشده',
    }
    return mapping.get(errand.status, errand.get_status_display())


def errand_summary_line(errand):
    return '%s\n%s\n🚗 %s' % (
        errand_status_badge(errand),
        dt_label(errand.start_time) + ' → ' + fa_digits(clock(errand.end_time)),
        (errand.reason or '—')[:160],
    )


# ============================================================
#  زیرساخت حرفه‌ای: Audit، نرخ پیام، صفحه‌بندی
# ============================================================

RATE_LIMIT_PER_MINUTE = 30


def audit(actor, action, target_type='', target_id='', details=''):
    """ثبت ردپای اقدام؛ خطای لاگ هرگز عملیات اصلی را نمی‌شکند."""
    from .models import AuditLog
    try:
        AuditLog.objects.create(
            actor=actor if getattr(actor, 'pk', None) else None,
            action=action,
            target_type=target_type or '',
            target_id=str(target_id or ''),
            details=(details or '')[:2000],
        )
    except Exception:  # pragma: no cover — فقط برای مقاوم‌سازی
        import logging
        logging.getLogger('balebot').exception('ثبت AuditLog ناموفق بود')


def rate_limit_ok(account):
    """حداکثر ۳۰ پیام در دقیقه برای هر حساب بله."""
    now = timezone.now()
    window = account.rate_window_start
    if not window or (now - window).total_seconds() > 60:
        account.rate_window_start = now
        account.rate_count = 1
        account.save(update_fields=['rate_window_start', 'rate_count'])
        return True

    account.rate_count += 1
    account.save(update_fields=['rate_count'])
    return account.rate_count <= RATE_LIMIT_PER_MINUTE


def paginate(items, page, per_page=5):
    """صفحه‌بندی سادهٔ لیست‌ها برای دکمه‌های inline."""
    items = list(items)
    total = len(items)
    pages = max(1, (total + per_page - 1) // per_page)
    try:
        page = int(page)
    except (TypeError, ValueError):
        page = 1
    page = min(max(page, 1), pages)
    start = (page - 1) * per_page
    return items[start:start + per_page], page, pages


# ============================================================
#  گزارش هفتگی و خروجی اکسل
# ============================================================

def week_bounds():
    today = timezone.localdate()
    return today - dt.timedelta(days=6), today


def _day_row(user, day):
    events = list(Time.objects.filter(user=user, date=day).order_by('time'))
    seconds = _worked_seconds(events)
    present = Present.objects.filter(user=user, date=day, present=True).exists()
    first_in = next((ev.time for ev in events if not ev.out), None)
    last_out = next((ev.time for ev in reversed(events) if ev.out), None)
    errands = list(
        Errand.objects.approved().filter(user=user).for_window(day, day)
    )
    return {
        'date': day,
        'present': present,
        'first_in': first_in,
        'last_out': last_out,
        'seconds': seconds,
        'errands': errands,
    }


def week_summary_text(user):
    start, today = week_bounds()
    lines = ['📅 گزارش ۷ روز اخیر', '']
    total = 0
    active_days = 0
    for offset in range(7):
        day = start + dt.timedelta(days=offset)
        row = _day_row(user, day)
        total += row['seconds']
        if row['present'] or row['seconds'] or row['errands']:
            active_days += 1
        lines.append(
            '%s\n  %s | %s → %s | %s%s'
            % (
                jalali_full(day),
                '✅ حاضر' if row['present'] else '—',
                fa_digits(clock(row['first_in'])) if row['first_in'] else '—',
                fa_digits(clock(row['last_out'])) if row['last_out'] else '—',
                format_hours(row['seconds']),
                (' | 🚗 %s مأموریت' % fa_digits(len(row['errands']))) if row['errands'] else '',
            )
        )
    lines += [
        '',
        '🗓 روزهای با فعالیت: %s' % fa_digits(active_days),
        '⏱ مجموع ۷ روز: %s' % format_hours(total),
    ]
    return '\n'.join(lines)


def build_user_report_xlsx(user, start, end):
    """خروجی اکسل گزارش روزانهٔ یک کاربر در یک بازه."""
    from recognition.excel_export import build_xlsx

    rows = []
    cursor = start
    total_seconds = 0
    while cursor <= end:
        row = _day_row(user, cursor)
        total_seconds += row['seconds']
        j = jdatetime.date.fromgregorian(date=cursor)
        rows.append([
            j.strftime('%Y/%m/%d'),
            jalali_full(cursor).split()[0],
            clock(row['first_in']) if row['first_in'] else '—',
            clock(row['last_out']) if row['last_out'] else '—',
            format_hours(row['seconds']),
            'حاضر' if row['present'] else '—',
            '، '.join(
                (e.destination or e.reason or 'مأموریت') for e in row['errands']
            ) or '—',
        ])
        cursor += dt.timedelta(days=1)

    rows.append(['', '', '', '', format_hours(total_seconds), '', ''])
    book = build_xlsx(
        sheet_name='گزارش حضور',
        headers=['تاریخ', 'روز', 'اولین ورود', 'آخرین خروج', 'کارکرد', 'حضور', 'مأموریت'],
        rows=rows,
        widths=[14, 16, 14, 14, 22, 12, 34],
        rtl=True,
    )
    filename = 'report-%s-%s.xlsx' % (user.get_username(), end.isoformat())
    return book, filename


# ============================================================
#  آمار ربات
# ============================================================

def bot_stats_text():
    from .models import AuditLog, BaleAccount, BotState

    total = BaleAccount.objects.count()
    linked = BaleAccount.objects.filter(django_user__isnull=False).count()
    blocked = BaleAccount.objects.filter(is_blocked=True).count()
    state = BotState.objects.filter(key='updates').first()
    pending = Errand.objects.pending().count()
    errors = CheckOutError.objects.filter(
        status=CheckOutError.STATUS_PENDING,
    ).count()
    audits = AuditLog.objects.count()

    return '\n'.join([
        '🤖 آمار ربات بله',
        '',
        '👥 حساب‌های بله: %s' % fa_digits(total),
        '🔗 متصل به سامانه: %s' % fa_digits(linked),
        '⛔ مسدود: %s' % fa_digits(blocked),
        '📨 آخرین update: %s' % fa_digits(state.last_update_id if state else 0),
        '⏳ مأموریت در انتظار: %s' % fa_digits(pending),
        '⚠️ خطای خروج باز: %s' % fa_digits(errors),
        '📝 رکورد Audit: %s' % fa_digits(audits),
    ])


def audit_list_text(page=1, per_page=10):
    """آخرین اقدامات، صفحه‌بندی‌شده."""
    from .models import AuditLog

    qs = AuditLog.objects.select_related('actor').all()
    items, page, pages = paginate(qs, page, per_page=per_page)
    lines = ['📝 ردپای اقدامات (صفحهٔ %s از %s)' % (fa_digits(page), fa_digits(pages)), '']
    if not items:
        lines.append('هیچ اقدامی ثبت نشده است.')
    for item in items:
        actor = item.actor.get_username() if item.actor else 'سیستم'
        local = timezone.localtime(item.created_at)
        lines.append(
            '• %s | %s\n  %s %s\n  %s'
            % (
                local.strftime('%m/%d %H:%M'),
                actor,
                item.action,
                ('#%s' % item.target_id) if item.target_id else '',
                (item.details or '')[:120],
            )
        )
    return '\n'.join(lines), page, pages


# ============================================================
#  فاز ۲: ویرایش مأموریت و قالب‌های سریع
# ============================================================

def recent_reasons(user, limit=4):
    """آخرین دلیل‌های یکتای مأموریت‌های کاربر (برای دکمه‌های سریع)."""
    seen = []
    for reason in (
        Errand.objects.filter(user=user).exclude(reason='')
        .order_by('-created_at').values_list('reason', flat=True)
    ):
        reason = (reason or '').strip()
        if reason and reason not in seen:
            seen.append(reason)
        if len(seen) >= limit:
            break
    return seen


def recent_destinations(user, limit=4):
    """آخرین مقصدهای یکتای مأموریت‌های کاربر."""
    seen = []
    for dest in (
        Errand.objects.filter(user=user).exclude(destination='')
        .order_by('-created_at').values_list('destination', flat=True)
    ):
        dest = (dest or '').strip()
        if dest and dest not in seen:
            seen.append(dest)
        if len(seen) >= limit:
            break
    return seen


def errand_payload(errand, **overrides):
    """payload فرم را از یک مأموریت موجود می‌سازد و فیلدهای دلخواه را عوض می‌کند."""
    payload = {
        'start': to_jalali_string(errand.start_time),
        'end': to_jalali_string(errand.end_time),
        'reason': errand.reason or '',
        'destination': errand.destination or '',
        'contact': errand.contact or '',
    }
    payload.update(overrides)
    return payload


def update_errand(errand, payload):
    """ویرایش مأموریت با همان اعتبارسنجی فرم (نسخهٔ ادمین: بدون سقف ۷ روز)."""
    from errands.forms import ErrandAdminEditForm

    form = ErrandAdminEditForm(
        instance=errand, data=payload, user=errand.user,
    )
    if not form.is_valid():
        for errors in form.errors.values():
            return None, errors[0]
        return None, 'اطلاعات واردشده معتبر نیست.'

    obj = form.save(commit=False)
    obj.save()
    return obj, None


# ============================================================
#  فاز ۳: پروندهٔ ۳۶۰، گزارش تیمی و پیام هدفمند
# ============================================================

def _month_hours(user, start, today):
    rows = (
        Time.objects.filter(user=user, date__gte=start, date__lte=today)
        .order_by('date', 'time')
    )
    total = 0
    for _day, events in itertools.groupby(rows, key=lambda ev: ev.date):
        total += _worked_seconds(list(events))
    return total


def user_360_text(user):
    """پروندهٔ فشردهٔ یک کاربر برای مدیر."""
    start, today, label = month_bounds()
    present_days = Present.objects.filter(
        user=user, date__gte=start, date__lte=today, present=True,
    ).count()
    hours = _month_hours(user, start, today)
    approved = Errand.objects.approved().filter(user=user).for_window(start, today)
    pending = Errand.objects.pending().filter(user=user).count()
    snapshots = user.attendance_snapshots.count()
    last_errand = (
        Errand.objects.filter(user=user).order_by('-start_time').first()
    )

    todays = list(Time.objects.filter(user=user, date=today).order_by('time'))
    first_in = next((ev.time for ev in todays if not ev.out), None)
    last_out = next((ev.time for ev in reversed(todays) if ev.out), None)

    lines = [
        '👤 %s' % user_label(user),
        '🆔 %s' % user.get_username(),
        '🎭 نقش: %s | %s' % (
            'سوپرادمین' if user.is_superuser else {
                ROLE_USER: 'کاربر', ROLE_ADMIN: 'مدیر', ROLE_BOTH: 'کاربر + مدیر',
            }.get(get_role(user), get_role(user)),
            '✅ فعال' if user.is_active else '⛔ غیرفعال',
        ),
        '📅 آخرین ورود: %s' % (dt_label(user.last_login) if user.last_login else '—'),
        '',
        '📊 %s' % label,
        '✅ حضور: %s روز | ⏱ کارکرد: %s' % (
            fa_digits(present_days), format_hours(hours),
        ),
        '🚗 مأموریت تأییدشده: %s | ⏳ در انتظار: %s' % (
            fa_digits(approved.count()), fa_digits(pending),
        ),
        '📸 عکس لحظه: %s' % fa_digits(snapshots),
        '',
        '🕒 امروز: ورود %s | خروج %s' % (
            fa_digits(clock(first_in)) if first_in else '—',
            fa_digits(clock(last_out)) if last_out else '—',
        ),
    ]
    if last_errand:
        lines.append('آخرین مأموریت: %s — %s' % (
            dt_label(last_errand.start_time), last_errand.reason[:60],
        ))
    return '\n'.join(lines)


def _user_month_stats(user, start, today):
    rows = (
        Time.objects.filter(user=user, date__gte=start, date__lte=today)
        .order_by('date', 'time')
    )
    total = 0
    late = 0
    overtime = 0
    active_days = 0
    for _day, events in itertools.groupby(rows, key=lambda ev: ev.date):
        events = list(events)
        active_days += 1
        total += _worked_seconds(events)
        first_in = next((ev.time for ev in events if not ev.out), None)
        last_out = next((ev.time for ev in reversed(events) if ev.out), None)
        if first_in and timezone.localtime(first_in).time() > dt.time(9, 0):
            late += 1
        if last_out and timezone.localtime(last_out).time() > dt.time(18, 0):
            overtime += 1
    present_days = Present.objects.filter(
        user=user, date__gte=start, date__lte=today, present=True,
    ).count()
    approved = Errand.objects.approved().filter(user=user).for_window(start, today)
    return {
        'total': total,
        'late': late,
        'overtime': overtime,
        'active_days': active_days,
        'present_days': present_days,
        'errand_seconds': sum(e.duration_seconds for e in approved),
        'errand_count': approved.count(),
    }


def team_report_text():
    today = timezone.localdate()
    start, _today, label = month_bounds()

    active_users = list(User.objects.filter(is_active=True).only('id'))
    active_ids = [u.pk for u in active_users]
    present_ids = set(
        Present.objects.filter(
            date=today, present=True, user_id__in=active_ids,
        ).values_list('user_id', flat=True)
    )

    todays = (
        Time.objects.filter(date=today, user_id__in=active_ids)
        .order_by('user_id', 'time')
    )
    open_count = 0
    for _user_id, events in itertools.groupby(todays, key=lambda ev: ev.user_id):
        _sessions, open_time = pair_time_events(list(events))
        if open_time:
            open_count += 1

    month_seconds = 0
    for user in User.objects.filter(is_active=True).only('id'):
        month_seconds += _month_hours(user, start, today)

    pending = Errand.objects.pending().count()
    errors = CheckOutError.objects.filter(
        status=CheckOutError.STATUS_PENDING,
    ).count()
    avg = month_seconds // max(len(active_users), 1)

    return '\n'.join([
        '📊 گزارش تیمی — %s' % today_jalali(),
        '',
        '👥 کاربران فعال: %s' % fa_digits(len(active_users)),
        '✅ حاضر امروز: %s' % fa_digits(len(present_ids)),
        '⛔ غایب: %s' % fa_digits(max(len(active_users) - len(present_ids), 0)),
        '🟡 بدون خروج: %s' % fa_digits(open_count),
        '',
        '📅 %s' % label,
        '⏱ مجموع کارکرد: %s' % format_hours(month_seconds),
        '📈 میانگین هر نفر: %s' % format_hours(avg),
        '',
        '⏳ مأموریت در انتظار: %s' % fa_digits(pending),
        '⚠️ خطای خروج باز: %s' % fa_digits(errors),
    ])


def build_team_report_xlsx(start, end):
    from recognition.excel_export import build_xlsx

    rows = []
    for user in User.objects.filter(is_active=True).order_by('username'):
        stats = _user_month_stats(user, start, end)
        rows.append([
            user.get_username(),
            user_label(user),
            stats['present_days'],
            format_hours(stats['total']),
            stats['late'],
            stats['overtime'],
            stats['errand_count'],
            format_hours(stats['errand_seconds']),
        ])
    book = build_xlsx(
        sheet_name='گزارش تیمی',
        headers=[
            'نام کاربری', 'نام', 'روز حضور', 'کارکرد ماه',
            'تأخیر', 'اضافه‌کاری', 'مأموریت', 'ساعت مأموریت',
        ],
        rows=rows,
        widths=[16, 28, 12, 22, 10, 12, 10, 20],
        rtl=True,
    )
    filename = 'team-report-%s.xlsx' % end.isoformat()
    return book, filename


def send_audience(client, audience, text, target_user=None, exclude_account=None):
    """ارسال پیام به مخاطب هدفمند. خروجی: (موفق، ناموفق)."""
    qs = (
        BaleAccount.objects
        .filter(is_blocked=False, django_user__isnull=False)
        .select_related('django_user')
    )
    sent = failed = 0
    for account in qs:
        if exclude_account is not None and account.pk == exclude_account.pk:
            continue
        user = account.django_user
        if audience == 'admins' and not is_admin_user(user):
            continue
        if audience == 'users' and is_admin_user(user):
            continue
        if audience == 'user' and (
            not target_user or user.pk != target_user.pk
        ):
            continue
        if safe_send(client, account, text) is not None:
            sent += 1
        else:
            failed += 1
    return sent, failed


def analytics_text():
    """تحلیل ماه جاری: تأخیر، اضافه‌کاری و روند."""
    start, today, label = month_bounds()
    users = list(User.objects.filter(is_active=True).order_by('username'))

    per_user = []
    total_seconds = 0
    late_total = 0
    overtime_total = 0
    absent_users = []
    for user in users:
        stats = _user_month_stats(user, start, today)
        total_seconds += stats['total']
        late_total += stats['late']
        overtime_total += stats['overtime']
        if stats['present_days'] == 0 and stats['active_days'] == 0:
            absent_users.append(user)
        per_user.append((user, stats))

    per_user.sort(key=lambda item: item[1]['total'], reverse=True)
    top = per_user[:3]
    avg = total_seconds // max(len(users), 1)

    lines = [
        '📈 تحلیل ماه — %s' % label,
        '',
        '⏱ مجموع کارکرد: %s' % format_hours(total_seconds),
        '📈 میانگین هر نفر: %s' % format_hours(avg),
        '⏰ روزهای تأخیر (ورود بعد از ۰۹:۰۰): %s' % fa_digits(late_total),
        '🌙 روزهای اضافه‌کاری (خروج بعد از ۱۸:۰۰): %s' % fa_digits(overtime_total),
        '',
        '🏆 بیشترین کارکرد:',
    ]
    for user, stats in top:
        lines.append('  • %s — %s' % (user_label(user), format_hours(stats['total'])))

    if absent_users:
        lines.append('')
        lines.append('⚠️ بدون هیچ رکورد این ماه:')
        for user in absent_users[:8]:
            lines.append('  • %s' % user_label(user))

    return '\n'.join(lines)


# ============================================================
#  پیام‌های زمان‌بندی‌شده
# ============================================================

def send_scheduled_due(client, now=None):
    """ارسال پیام‌های زمان‌بندی‌شده‌ای که موعدشان رسیده است."""
    from .models import ScheduledMessage

    now = now or timezone.now()
    due = (
        ScheduledMessage.objects
        .filter(sent_at__isnull=True, send_at__lte=now)
        .select_related('target_user')
    )
    count = 0
    for msg in due:
        if msg.audience == ScheduledMessage.AUDIENCE_USER and not msg.target_user:
            msg.sent_at = now
            msg.save(update_fields=['sent_at'])
            continue
        sent, failed = send_audience(
            client,
            msg.audience,
            '📢 پیام زمان‌بندی‌شده:\n\n' + msg.text,
            target_user=msg.target_user,
        )
        msg.sent_at = now
        msg.save(update_fields=['sent_at'])
        audit(None, 'scheduled_send', 'scheduled', msg.pk,
              'ارسال: %d | ناموفق: %d' % (sent, failed))
        count += 1
    return count
