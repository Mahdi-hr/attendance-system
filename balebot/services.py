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
