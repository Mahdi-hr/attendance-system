"""یادآورها و هشدارهای خودکار ربات بله.

چهار یادآور:
۱) فراموشی خروج — بعد از ساعت پایان کار + مهلت تنظیم‌شده.
۲) مأموریت نزدیک — چند دقیقه قبل از شروع مأموریت تأییدشده.
۳) صف تأیید — خلاصهٔ روزانه برای مدیران.
۴) هشدار ناهنجاری — حضور باز بیش از ۱۴ ساعت (فراموشی خروج جدی).

هر یادآور با یک کلید یکتا در `ReminderLog` ثبت می‌شود تا دوبار ارسال نشود.
"""

import datetime as dt
import itertools
import logging

from django.utils import timezone

from errands.models import Errand
from recognition.reports import clock, fa_digits, pair_time_events
from users.models import Time
from users.roles import is_admin_user

from . import services
from .models import BaleAccount, BotConfig, ReminderLog

logger = logging.getLogger('balebot')

LONG_OPEN_HOURS = 14


def _first_time(key):
    """اگر این کلید قبلاً ثبت نشده باشد True برمی‌گرداند و ثبتش می‌کند."""
    _obj, created = ReminderLog.objects.get_or_create(key=key)
    return created


def _linked_accounts(**filters):
    return (
        BaleAccount.objects
        .filter(is_blocked=False, django_user__isnull=False, **filters)
        .select_related('django_user')
    )


def _checkout_reminders(client, config, now, today):
    """به کسانی که بعد از ساعت کاری هنوز خروج نزده‌اند یادآوری می‌کند."""
    work_end = timezone.make_aware(
        dt.datetime.combine(today, config.work_end_time),
        timezone.get_current_timezone(),
    )
    threshold = work_end + dt.timedelta(minutes=config.checkout_reminder_after_minutes)
    if now < threshold:
        return 0

    sent = 0
    accounts = _linked_accounts(notify_daily=True, django_user__is_active=True)
    for account in accounts:
        events = list(
            Time.objects.filter(user=account.django_user, date=today).order_by('time')
        )
        _sessions, open_time = pair_time_events(events)
        if not open_time:
            continue

        key = 'checkout:%s:%s' % (account.pk, today.isoformat())
        if not _first_time(key):
            continue

        ok = services.safe_send(
            client,
            account,
            '⏰ یادآور خروج\n\n'
            'شما از ساعت %s در حال حضور هستید و هنوز خروج ثبت نکرده‌اید.\n'
            'اگر کارتان تمام شده، خروج را ثبت کنید.'
            % fa_digits(clock(open_time)),
        )
        if ok is not None:
            sent += 1
    return sent


def _upcoming_errand_reminders(client, config, now):
    """مأموریت‌های تأییدشده‌ای که تا چند دقیقهٔ دیگر شروع می‌شوند."""
    horizon = now + dt.timedelta(minutes=config.upcoming_errand_minutes)
    errands = (
        Errand.objects.approved()
        .filter(start_time__gt=now, start_time__lte=horizon, end_time__gt=now)
        .select_related('user')
        .order_by('start_time')
    )

    sent = 0
    for errand in errands:
        key = 'errand:%s' % errand.pk
        if not _first_time(key):
            continue
        accounts = _linked_accounts(
            django_user=errand.user, notify_errand=True,
            django_user__is_active=True,
        )
        for account in accounts:
            ok = services.safe_send(
                client,
                account,
                '🚗 یادآور مأموریت\n\n'
                'مأموریت شما %s دقیقهٔ دیگر شروع می‌شود.\n'
                '🕒 شروع: %s\n📝 %s\n🚗 %s'
                % (
                    fa_digits(config.upcoming_errand_minutes),
                    services.dt_label(errand.start_time),
                    errand.reason,
                    errand.destination or '—',
                ),
            )
            if ok is not None:
                sent += 1
    return sent


def _pending_digest(client, config, now, today):
    """خلاصهٔ روزانهٔ صف تأیید برای مدیران."""
    target = timezone.make_aware(
        dt.datetime.combine(today, config.pending_digest_time),
        timezone.get_current_timezone(),
    )
    # پنجرهٔ ۱۰ دقیقه‌ای؛ cron هر ۵ دقیقه اجرا می‌شود.
    if now < target or (now - target).total_seconds() > 600:
        return 0

    pending = list(
        Errand.objects.pending().select_related('user').order_by('start_time')[:5]
    )
    if not pending:
        return 0

    key = 'pending:%s' % today.isoformat()
    if not _first_time(key):
        return 0

    total = Errand.objects.pending().count()
    lines = [
        '⏳ یادآور صف تأیید',
        '',
        '%s مأموریت در انتظار بررسی است:' % fa_digits(total),
        '',
    ]
    for errand in pending:
        lines.append(
            '• %s — %s'
            % (services.user_label(errand.user), (errand.reason or '—')[:60])
        )
    if total > len(pending):
        lines.append('… و %s مورد دیگر' % fa_digits(total - len(pending)))
    text = '\n'.join(lines)

    sent = 0
    for account in _linked_accounts(notify_daily=True, django_user__is_active=True):
        if is_admin_user(account.django_user):
            ok = services.safe_send(client, account, text)
            if ok is not None:
                sent += 1
    return sent


def _anomaly_digest(client, now, today):
    """هشدار حضور باز بیش از ۱۴ ساعت (احتمال فراموشی خروج جدی)."""
    threshold = now - dt.timedelta(hours=LONG_OPEN_HOURS)
    alerts = []

    accounts = _linked_accounts(django_user__is_active=True)
    for account in accounts:
        events = list(
            Time.objects.filter(
                user=account.django_user,
                date__gte=today - dt.timedelta(days=1),
            ).order_by('date', 'time')
        )
        for _day, day_events in itertools.groupby(events, key=lambda ev: ev.date):
            _sessions, open_time = pair_time_events(list(day_events))
            if open_time and open_time < threshold:
                alerts.append((account.django_user, open_time))
                break

    if not alerts:
        return 0

    key = 'anomaly:%s' % today.isoformat()
    if not _first_time(key):
        return 0

    lines = [
        '🚨 هشدار ناهنجاری',
        '',
        '%s نفر بیش از %s ساعت در حال حضور مانده‌اند و خروج ثبت نکرده‌اند:'
        % (fa_digits(len(alerts)), fa_digits(LONG_OPEN_HOURS)),
        '',
    ]
    for user, open_time in alerts[:10]:
        lines.append(
            '• %s — از %s'
            % (services.user_label(user), services.dt_label(open_time))
        )

    text = '\n'.join(lines)
    sent = 0
    for account in _linked_accounts(notify_daily=True, django_user__is_active=True):
        if is_admin_user(account.django_user):
            ok = services.safe_send(client, account, text)
            if ok is not None:
                sent += 1
    return sent


def run_reminders(client, now=None, dry_run=False):
    """اجرای همهٔ یادآورها؛ خروجی: آمار ارسال‌شده‌ها."""
    config = BotConfig.get_solo()
    if not config.bot_enabled or not config.reminders_enabled:
        return {
            'checkout': 0, 'upcoming': 0, 'pending': 0,
            'anomalies': 0, 'skipped': True,
        }

    now = now or timezone.now()
    today = timezone.localdate(now)

    if dry_run:
        # در حالت آزمایشی فقط کاندیدها شمرده می‌شوند و چیزی ارسال/ثبت نمی‌شود.
        work_end = timezone.make_aware(
            dt.datetime.combine(today, config.work_end_time),
            timezone.get_current_timezone(),
        )
        threshold = work_end + dt.timedelta(
            minutes=config.checkout_reminder_after_minutes,
        )
        checkout = 0
        if now >= threshold:
            for account in _linked_accounts(
                notify_daily=True, django_user__is_active=True,
            ):
                events = list(
                    Time.objects.filter(
                        user=account.django_user, date=today,
                    ).order_by('time')
                )
                _sessions, open_time = pair_time_events(events)
                if open_time:
                    checkout += 1
        horizon = now + dt.timedelta(minutes=config.upcoming_errand_minutes)
        upcoming = Errand.objects.approved().filter(
            start_time__gt=now, start_time__lte=horizon, end_time__gt=now,
        ).count()
        return {
            'checkout': checkout,
            'upcoming': upcoming,
            'pending': Errand.objects.pending().count(),
            'anomalies': 0,
            'dry_run': True,
        }

    result = {
        'checkout': _checkout_reminders(client, config, now, today),
        'upcoming': _upcoming_errand_reminders(client, config, now),
        'pending': _pending_digest(client, config, now, today),
        'anomalies': _anomaly_digest(client, now, today),
    }
    return result
