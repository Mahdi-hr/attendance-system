from users.models import Present, Time, Break
from errands.services import (
    build_raw_map as errand_map,
    effective_seconds,
    subtract_intervals,
)
import jdatetime
import datetime as dt


FA_DIGITS = str.maketrans('0123456789', '۰۱۲۳۴۵۶۷۸۹')


def fa_digits(value):
    return str(value).translate(FA_DIGITS)


def format_hours(seconds):
    seconds = max(int(seconds or 0), 0)
    total_minutes = round(seconds / 60)
    hours = total_minutes // 60
    minutes = total_minutes % 60
    if hours > 0:
        return f"{fa_digits(hours)} ساعت و {fa_digits(minutes)} دقیقه"
    return f"{fa_digits(minutes)} دقیقه"


def pair_time_events(events):
    """Pair ordered in/out events into closed sessions.

    Robust to messy data: duplicate consecutive 'in' events are ignored,
    'out' events without an open session are ignored, and a trailing lone
    'in' becomes the open (unfinished) session.

    Returns (sessions, open_time) where sessions is a list of
    (in_datetime, out_datetime) tuples and open_time is the datetime of the
    still-open session or None.
    """
    sessions = []
    open_time = None
    for ev in events:
        if ev.out:
            if open_time is not None:
                sessions.append((open_time, ev.time))
                open_time = None
        else:
            if open_time is None:
                open_time = ev.time
    return sessions, open_time


def _errand_labels(intervals, errands):
    """ساخت برچسب‌های خوانا برای بازه و دلایل معموریت.

    `intervals` بازه‌های **مؤثر** همان روز است (معموریت منهای حضور).
    خروجی:
        range_text : مثل «۰۹:۰۰ تا ۱۲:۳۰» یا «۰۹:۰۰ تا ۱۲:۳۰ و ۱۴:۰۰ تا ۱۷:۰۰»
        reasons    : لیست دلایل معموریت‌های آن روز برای tooltip
    """
    if not intervals:
        return '', []

    parts = []
    for start, end in intervals:
        parts.append('%s تا %s' % (
            fa_digits(start.strftime('%H:%M')),
            fa_digits(end.strftime('%H:%M')),
        ))

    reasons = []
    for errand in errands:
        text = (getattr(errand, 'reason', '') or '').strip()
        if text and text not in reasons:
            reasons.append(text)

    return ' و '.join(parts), reasons


def _errand_note(errands):
    """یادداشت کوتاه معموریت برای چاپ: «مأموریت: بانک — بایگانی»."""
    if not errands:
        return ''
    labels = []
    for errand in errands:
        reason = (getattr(errand, 'reason', '') or '').strip()
        destination = (getattr(errand, 'destination', '') or '').strip()
        if destination and reason:
            labels.append('%s (%s)' % (destination, reason))
        elif destination:
            labels.append(destination)
        elif reason:
            labels.append(reason)
    if not labels:
        return 'مأموریت'
    return 'مأموریت: ' + ' | '.join(labels)


def daily_rows(users, date_from=None, date_to=None):
    """Build per (user, date) attendance rows.

    `date_from`/`date_to` are optional gregorian date bounds; when either is
    None that bound is unbounded (so both None = all data in the database).

    هر ردیف علاوه بر «مدت حضور» (ساعت فیزیکی ورود/خروج) دو چیز دیگر هم دارد:

        errand_seconds : مدت معموریتِ **مؤثر** (تأییدشده، منهای همپوشانی با حضور)
        total_seconds  : ساعت کار روز = work_seconds + errand_seconds

    قاعده: اجتماعِ (حضور ∪ معموریت) = ساعت کار. چون مدت معموریت از حضور
    فیزیکی کسر می‌شود، هیچ دقیقه‌ای دوبار شمرده نمی‌شود.

    Returns a list of dicts ordered by date then username:
      user, date, present, events, sessions, open_time,
      first_in, last_out, work_seconds, work_text, total_seconds, total_text,
      errands, errand_seconds, errand_text, errand_label,
      breaks, break_text, exit_reason, exit_reason_text
    """
    time_qs = (
        Time.objects
        .filter(user__in=users)
        .select_related('user', 'exit_reason')
        .order_by('user_id', 'date', 'time', 'id')
    )
    if date_from is not None:
        time_qs = time_qs.filter(date__gte=date_from)
    if date_to is not None:
        time_qs = time_qs.filter(date__lte=date_to)

    present_qs = Present.objects.filter(user__in=users)
    if date_from is not None:
        present_qs = present_qs.filter(date__gte=date_from)
    if date_to is not None:
        present_qs = present_qs.filter(date__lte=date_to)
    present_map = {}
    for p in present_qs:
        present_map[(p.user_id, p.date)] = p.present

    break_qs = Break.objects.filter(user__in=users).order_by('break_out')
    if date_from is not None:
        break_qs = break_qs.filter(date__gte=date_from)
    if date_to is not None:
        break_qs = break_qs.filter(date__lte=date_to)
    break_map = {}
    for b in break_qs:
        break_map.setdefault((b.user_id, b.date), []).append(b)

    exit_reason_map = {}
    for ev in time_qs:
        if ev.out and (ev.exit_reason or ev.exit_reason_text):
            exit_reason_map[(ev.user_id, ev.date)] = {
                'exit_reason': ev.exit_reason,
                'exit_reason_text': ev.exit_reason_text
            }

    # ---- معموریت‌های تأییدشده، تفکیک‌شده به روز ----
    # فقط تأییدشده‌ها وارد محاسبه می‌شوند؛ درخواست‌های «در انتظار بررسی»
    # هیچ اثری روی گزارش ندارند.
    errand_raw = errand_map(users, date_from, date_to)

    by_key = {}
    for ev in time_qs:
        key = (ev.user_id, ev.date)
        by_key.setdefault(key, []).append(ev)

    rows = []
    # کلیدهای کاربرانی که فقط معموریت تأییدشده دارند و هیچ رویداد حضوری
    # ثبت نکرده‌اند هم باید در گزارش بیایند.
    for key in errand_raw:
        by_key.setdefault(key, [])

    for key, events in by_key.items():
        user_id, date = key
        user = events[0].user if events else next(
            (u for u in users if u.id == user_id), None
        )
        if user is None:
            continue
        events = [e for e in events if e.time is not None]
        sessions, open_time = pair_time_events(events) if events else ([], None)
        work_seconds = sum((o - i).total_seconds() for i, o in sessions)
        first_in = next((e.time for e in events if not e.out), None)
        last_out = next((e.time for e in reversed(events) if e.out), None)
        breaks = break_map.get(key, [])
        exit_info = exit_reason_map.get(key, {})

        # معموریت مؤثر = بازهٔ معموریت منهای بازه‌های حضور فیزیکی
        raw = errand_raw.get(key, {})
        errand_intervals = raw.get('intervals', [])
        errand_objects = raw.get('errands', [])
        if errand_intervals:
            errand_seconds = effective_seconds(errand_intervals, sessions)
            effective = subtract_intervals(errand_intervals, sessions)
        else:
            errand_seconds = 0
            effective = []

        # برچسب‌های آمادهٔ نمایش برای قالب و چاپ
        errand_range_text, errand_reasons = _errand_labels(effective, errand_objects)

        rows.append({
            'user': user,
            'username': user.username,
            'date': date,
            # کسی که معموریت تأییدشده دارد، در آن روز «حاضر» شمرده می‌شود
            # حتی اگر ورود فیزیکی ثبت نکرده باشد.
            'present': bool(events) or bool(errand_intervals)
                      or present_map.get(key, False),
            'events': events,
            'sessions': sessions,
            'open_time': open_time,
            'first_in': first_in,
            'last_out': last_out,
            'end_of_day': None,
            'work_seconds': work_seconds,
            'work_text': format_hours(work_seconds),
            'errands': errand_objects,
            'errand_intervals': effective,
            'errand_seconds': errand_seconds,
            'errand_text': format_hours(errand_seconds),
            'errand_range_text': errand_range_text,
            'errand_reasons': errand_reasons,
            'errand_note': _errand_note(errand_objects),
            'has_errand': bool(effective),
            'total_seconds': work_seconds + errand_seconds,
            'total_text': format_hours(work_seconds + errand_seconds),
            'breaks': breaks,
            'break_text': format_hours(sum(b.duration_minutes() * 60 for b in breaks)),
            'exit_reason': exit_info.get('exit_reason'),
            'exit_reason_text': exit_info.get('exit_reason_text', ''),
        })

    for key in present_map:
        if key not in by_key and present_map[key]:
            user_id, date = key
            user = next((u for u in users if u.id == user_id), None)
            if user is None:
                continue
            exit_info = exit_reason_map.get(key, {})
            rows.append({
                'user': user,
                'username': user.username,
                'date': date,
                'present': True,
                'events': [],
                'sessions': [],
                'open_time': None,
                'first_in': None,
                'last_out': None,
                'end_of_day': None,
                'work_seconds': 0,
                'work_text': format_hours(0),
                'errands': [],
                'errand_intervals': [],
                'errand_seconds': 0,
                'errand_text': format_hours(0),
                'errand_range_text': '',
                'errand_reasons': [],
                'errand_note': '',
                'has_errand': False,
                'total_seconds': 0,
                'total_text': format_hours(0),
                'breaks': break_map.get(key, []),
                'break_text': format_hours(0),
                'exit_reason': exit_info.get('exit_reason'),
                'exit_reason_text': exit_info.get('exit_reason_text', ''),
            })

    rows.sort(key=lambda r: (r['date'], r['username']))
    return rows


def range_summary(rows):
    """Totals across a list of daily rows.

    مجموع ساعت روی **ساعت کار** حساب می‌شود (حضور + معموریت مؤثر).

    Returns (days_present, total_seconds, unique_person_count).
    """
    total_seconds = sum(r.get('total_seconds', r['work_seconds']) for r in rows)
    days_present = len({(r['username'], r['date']) for r in rows if r['present']})
    person_count = len({r['username'] for r in rows})
    return days_present, total_seconds, person_count


def errand_summary(rows):
    """مجموع مدت معموریت در یک لیست ردیف‌ها."""
    return sum(r.get('errand_seconds', 0) for r in rows)


def per_user_summary(rows):
    """Aggregate work_seconds and days_present per user.

    Returns a list of dicts with: user, username, first_name, last_name,
    days_present, total_seconds, total_text, errand_seconds, errand_text.
    """
    from collections import defaultdict
    agg = defaultdict(lambda: {
        'days': 0, 'seconds': 0, 'user_obj': None, 'errand': 0,
    })
    for r in rows:
        if not r['present']:
            continue
        key = r['username']
        agg[key]['days'] += 1
        agg[key]['seconds'] += r.get('total_seconds', r['work_seconds'])
        agg[key]['errand'] += r.get('errand_seconds', 0)
        if agg[key]['user_obj'] is None:
            agg[key]['user_obj'] = r['user']
    
    result = []
    for username, data in agg.items():
        user = data['user_obj']
        if user:
            result.append({
                'username': username,
                'first_name': user.first_name,
                'last_name': user.last_name,
                'days_present': data['days'],
                'total_seconds': data['seconds'],
                'total_text': format_hours(data['seconds']),
                'errand_seconds': data['errand'],
                'errand_text': format_hours(data['errand']),
            })
    # Sort by total_seconds descending (most hours first)
    result.sort(key=lambda x: x['total_seconds'], reverse=True)
    return result


def clock(dt):
    if not dt:
        return '-'
    from django.utils import timezone
    return timezone.localtime(dt).strftime('%H:%M')


def jalali_full(date):
    """Full Jalali date string with Persian weekday, e.g. دوشنبه ۲۵ مهر ۱۴۰۳."""
    if not date:
        return '-'
    j = jdatetime.date.fromgregorian(date=date)
    return '%s %s %s %s' % (
        WEEKDAY_FA[j.weekday()],
        fa_digits(j.day),
        MONTH_FA[j.month - 1],
        fa_digits(j.year),
    )


def timeline_text(row):
    """Human-readable in/out timeline used for Excel/print exports."""
    parts = []
    for ev in row['events']:
        if ev.time:
            parts.append(('خروج' if ev.out else 'ورود') + ' ' + clock(ev.time))
    if row['open_time']:
        parts.append('هنوز در محل (' + clock(row['open_time']) + ')')
    return ' | '.join(parts)


WEEKDAY_FA = ['شنبه', 'یکشنبه', 'دوشنبه', 'سه‌شنبه', 'چهارشنبه', 'پنجشنبه', 'جمعه']
MONTH_FA = ['فروردین', 'اردیبهشت', 'خرداد', 'تیر', 'مرداد', 'شهریور', 'مهر', 'آبان', 'آذر', 'دی', 'بهمن', 'اسفند']


def _to_jalali(d):
    return jdatetime.date.fromgregorian(date=d)


def _jalali_week_key(d):
    """Return (jy, week_number) where weeks start Saturday."""
    j = _to_jalali(d)
    weekday = d.weekday()  # Monday=0 ... Sunday=6
    jalali_weekday = (weekday + 2) % 7  # Saturday=0 ... Friday=6
    sat = d - dt.timedelta(days=jalali_weekday)
    j_sat = _to_jalali(sat)
    return (j_sat.year, j_sat.month, j_sat.day)


def _jalali_month_key(d):
    j = _to_jalali(d)
    return (j.year, j.month)


def weekly_summary(rows):
    """Group daily rows by Jalali week (Sat→Fri).

    Returns a list of dicts sorted by week, each containing:
      label: human-readable week label (e.g. 'هفته ۱۴۰۳/۰۷ شنبه ۱ تا جمعه ۷')
      week_start: gregorian date of Saturday
      week_end: gregorian date of Friday
      days: count of days with present=True
      seconds: total work_seconds
      hours_text: formatted hours
    """
    weeks = {}
    for r in rows:
        if not r['present']:
            continue
        key = _jalali_week_key(r['date'])
        if key not in weeks:
            weekday = r['date'].weekday()
            jalali_weekday = (weekday + 2) % 7
            sat = r['date'] - dt.timedelta(days=jalali_weekday)
            fri = sat + dt.timedelta(days=6)
            weeks[key] = {
                'week_start': sat,
                'week_end': fri,
                'days': 0,
                'seconds': 0,
                'errand': 0,
            }
        weeks[key]['days'] += 1
        weeks[key]['seconds'] += r.get('total_seconds', r['work_seconds'])
        weeks[key]['errand'] += r.get('errand_seconds', 0)

    result = []
    for key in sorted(weeks):
        w = weeks[key]
        j_sat = _to_jalali(w['week_start'])
        j_fri = _to_jalali(w['week_end'])
        label = '%s/%s تا %s/%s' % (
            fa_digits(j_sat.month), fa_digits(j_sat.day),
            fa_digits(j_fri.month), fa_digits(j_fri.day),
        )
        result.append({
            'label': label,
            'week_start': w['week_start'],
            'week_end': w['week_end'],
            'days': w['days'],
            'seconds': w['seconds'],
            'hours_text': format_hours(w['seconds']),
            'errand_seconds': w.get('errand', 0),
            'errand_text': format_hours(w.get('errand', 0)),
        })
    return result


def monthly_summary(rows):
    """Group daily rows by Jalali month.

    Returns a list of dicts sorted by month, each containing:
      label: human-readable month label (e.g. 'مهر ۱۴۰۳')
      year: Jalali year
      month: Jalali month number
      month_name: Persian month name
      days: count of days with present=True
      seconds: total work_seconds
      hours_text: formatted hours
    """
    months = {}
    for r in rows:
        if not r['present']:
            continue
        key = _jalali_month_key(r['date'])
        if key not in months:
            j = _to_jalali(r['date'])
            months[key] = {
                'year': j.year,
                'month': j.month,
                'month_name': MONTH_FA[j.month - 1],
                'days': 0,
                'seconds': 0,
                'errand': 0,
            }
        months[key]['days'] += 1
        months[key]['seconds'] += r.get('total_seconds', r['work_seconds'])
        months[key]['errand'] += r.get('errand_seconds', 0)

    result = []
    for key in sorted(months):
        m = months[key]
        label = '%s %s' % (m['month_name'], fa_digits(m['year']))
        result.append({
            'label': label,
            'year': m['year'],
            'month': m['month'],
            'month_name': m['month_name'],
            'days': m['days'],
            'seconds': m['seconds'],
            'hours_text': format_hours(m['seconds']),
            'errand_seconds': m.get('errand', 0),
            'errand_text': format_hours(m.get('errand', 0)),
        })
    return result