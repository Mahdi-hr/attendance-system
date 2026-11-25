"""منطق ثبت کارکرد پروژه‌ها و ساخت دادهٔ گزارش/اکسل."""

import datetime as dt
from collections import defaultdict
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

import jdatetime
from django.contrib.auth import get_user_model
from django.db import models, transaction
from django.utils import timezone

from errands.models import Errand
from errands.services import effective_seconds, split_interval_by_day
from recognition.reports import daily_rows

from .models import Project, WorkLog, WorkLogMonth

User = get_user_model()

MONTHS_FA = jdatetime.date.j_months_fa
WEEKDAY_SHORT = ['ش', 'ی', 'د', 'س', 'چ', 'پ', 'ج']
TWO_PLACES = Decimal('0.01')
MAX_DAILY_HOURS = Decimal('24')
TOLERANCE = Decimal('0.01')


def quantize(value):
    return Decimal(value).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def current_month():
    j = jdatetime.date.fromgregorian(date=timezone.localdate())
    return j.year, j.month


def month_bounds(year, month):
    """(اولین روز میلادی، آخرین روز میلادی، تعداد روزهای ماه شمسی)"""
    start_j = jdatetime.date(year, month, 1)
    if month == 12:
        next_j = jdatetime.date(year + 1, 1, 1)
    else:
        next_j = jdatetime.date(year, month + 1, 1)
    start = start_j.togregorian()
    next_start = next_j.togregorian()
    end = next_start - dt.timedelta(days=1)
    return start, end, (end - start).days + 1


def month_days(year, month):
    """فهرست روزهای ماه شمسی با تاریخ میلادی و نام روز."""
    start, end, count = month_bounds(year, month)
    days = []
    for day in range(1, count + 1):
        jd = jdatetime.date(year, month, day)
        days.append({
            'day': day,
            'date': jd.togregorian(),
            'weekday': WEEKDAY_SHORT[jd.weekday()],
            'is_friday': jd.weekday() == 6,
            'jalali': '%04d/%02d/%02d' % (year, month, day),
        })
    return days


def month_label(year, month):
    return '%s %s' % (MONTHS_FA[month - 1], year)


def user_display(user):
    full = ('%s %s' % (user.first_name, user.last_name)).strip()
    return full or user.get_username()


def attendance_map(user, year, month):
    """نقشهٔ تاریخ → ردیف حضور/کارکرد سیستم برای یک ماه."""
    start, end, _count = month_bounds(year, month)
    rows = daily_rows([user], start, end)
    return {row['date']: row for row in rows}


def errand_split_map(user, year, month, attendance):
    """ساعت مؤثر مأموریت‌های تأییدشده به تفکیک ریموت و شرکتی.

    فقط مأموریت‌های تأییدشده حساب می‌شوند. ساعت مؤثر = بازهٔ مأموریت منهای
    بازه‌های حضور فیزیکی، تا با ساعت حضور دوبار شمرده نشود.

    خروجی: {date: (remote_seconds, company_seconds)}
    """
    start, end, _count = month_bounds(year, month)
    sessions_by_day = {
        date: row.get('sessions', [])
        for date, row in attendance.items()
    }

    remote = defaultdict(list)
    company = defaultdict(list)
    errands = (
        Errand.objects.approved()
        .filter(user=user)
        .for_window(start, end)
    )
    for errand in errands:
        bucket = remote if errand.work_mode == Errand.MODE_REMOTE else company
        for date, seg_start, seg_end in split_interval_by_day(
            errand.start_time, errand.end_time,
        ):
            bucket[date].append((seg_start, seg_end))

    result = {}
    for date in set(remote) | set(company) | set(sessions_by_day):
        sessions = sessions_by_day.get(date, [])
        result[date] = (
            effective_seconds(remote.get(date, []), sessions),
            effective_seconds(company.get(date, []), sessions),
        )
    return result


def company_errand_map(user, year, month, attendance):
    """نقشهٔ تاریخ → {مقصد: ثانیهٔ مؤثر} برای مأموریت‌های شرکتی.

    برای نمایش مقصد در عنوان فعالیت (وب) و ساخت ردیف‌های تفکیک‌شده در
    اکسل استفاده می‌شود. ساعت مؤثر هر مقصد جداگانه محاسبه می‌شود تا با
    حضور فیزیکی دوبار شمرده نشود.
    """
    start, end, _count = month_bounds(year, month)
    sessions_by_day = {
        date: row.get('sessions', [])
        for date, row in attendance.items()
    }
    buckets = defaultdict(lambda: defaultdict(list))
    errands = (
        Errand.objects.approved()
        .filter(user=user, work_mode=Errand.MODE_COMPANY)
        .for_window(start, end)
    )
    for errand in errands:
        destination = (errand.destination or '').strip() or 'بدون مقصد'
        for date, seg_start, seg_end in split_interval_by_day(
            errand.start_time, errand.end_time,
        ):
            buckets[date][destination].append((seg_start, seg_end))

    result = {}
    for date, per_destination in buckets.items():
        sessions = sessions_by_day.get(date, [])
        result[date] = {
            destination: effective_seconds(intervals, sessions)
            for destination, intervals in per_destination.items()
        }
    return result


def allowed_hours_for(attendance, split, date):
    """ساعت قابل تخصیص به پروژه = حضور فیزیکی + مأموریت ریموت."""
    physical = allocatable_hours_for(attendance, date)
    remote_seconds = split.get(date, (0, 0))[0]
    return physical + _hours_from_seconds(remote_seconds)


def _hours_from_seconds(seconds):
    return quantize(Decimal(seconds or 0) / Decimal(3600))


def worked_hours_for(attendance, date):
    """کل ساعت کار روز = حضور فیزیکی + مأموریت (ریموت و شرکتی)."""
    row = attendance.get(date)
    if not row:
        return Decimal('0.00')
    return _hours_from_seconds(row.get('total_seconds'))


def allocatable_hours_for(attendance, date):
    """ساعت قابل تخصیص به پروژه‌ها = حضور فیزیکی (بدون مأموریت).

    ساعت مأموریت جداگانه و خودکار در گزارش می‌آید؛ بنابراین کاربر نباید
    آن را دوباره روی پروژه‌ها تخصیص بدهد.
    """
    row = attendance.get(date)
    if not row:
        return Decimal('0.00')
    return _hours_from_seconds(row.get('work_seconds'))


def errand_hours_for(attendance, date):
    """ساعت مأموریت مؤثرِ تأییدشده — خودکار و فقط‌خواندنی."""
    row = attendance.get(date)
    if not row:
        return Decimal('0.00')
    return _hours_from_seconds(row.get('errand_seconds'))


def month_matrix(user, year, month, projects=None):
    """دادهٔ ماتریس پروژه × روز برای نمایش و ویرایش.

    برای هر روز:
      worked            کل ساعت کار سیستم (حضور + ریموت + شرکتی)
      allocatable       ساعت قابل تخصیص به پروژه‌ها (حضور + ریموت)
      errand            ساعت مأموریت شرکتی (خودکار، فقط‌خواندنی)
      manual_allocated  جمع تخصیص دستی (پروژه‌ها + سایر)
      allocated         manual + errand
      remaining         worked - allocated
    """
    start, end, _count = month_bounds(year, month)
    days = month_days(year, month)
    attendance = attendance_map(user, year, month)
    split = errand_split_map(user, year, month, attendance)
    company_map = company_errand_map(user, year, month, attendance)
    errand_destinations = sorted({
        destination
        for per_destination in company_map.values()
        for destination in per_destination
    })
    if errand_destinations:
        shown = '، '.join(errand_destinations[:2])
        if len(errand_destinations) > 2:
            shown += '…'
        errand_label = 'مأموریت شرکتی — %s' % shown
    else:
        errand_label = 'مأموریت شرکتی (تأییدشده)'

    logs = (
        WorkLog.objects
        .filter(user=user, date__gte=start, date__lte=end)
        .select_related('project')
    )
    by_project = {}
    other = {}
    other_title = ''
    for log in logs:
        if log.project_id:
            by_project[(log.project_id, log.date)] = {
                'hours': log.hours, 'note': log.note or '',
            }
        else:
            other[log.date] = {
                'hours': log.hours, 'note': log.note or '',
            }
            if log.other_title:
                other_title = log.other_title

    if projects is None:
        projects = list(Project.objects.filter(is_active=True))
    else:
        projects = list(projects)

    project_rows = []
    for project in projects:
        cells = []
        total = Decimal('0')
        for d in days:
            entry = by_project.get((project.id, d['date']))
            value = entry['hours'] if entry else Decimal('0')
            total += value
            cells.append({
                'day': d['day'],
                'value': value,
                'note': entry['note'] if entry else '',
            })
        project_rows.append({
            'project': project,
            'cells': cells,
            'total': total,
        })

    other_cells = []
    other_total = Decimal('0')
    for d in days:
        entry = other.get(d['date'])
        value = entry['hours'] if entry else Decimal('0')
        other_total += value
        other_cells.append({
            'day': d['day'],
            'value': value,
            'note': entry['note'] if entry else '',
        })

    errand_cells = []
    errand_total = Decimal('0')
    for d in days:
        company_seconds = split.get(d['date'], (0, 0))[1]
        value = _hours_from_seconds(company_seconds)
        errand_total += value
        errand_cells.append({'day': d['day'], 'value': value})

    day_rows = []
    total_worked = Decimal('0')
    total_allocatable = Decimal('0')
    total_errand = Decimal('0')
    total_manual = Decimal('0')
    total_allocated = Decimal('0')
    for day in days:
        physical = allocatable_hours_for(attendance, day['date'])
        remote = _hours_from_seconds(split.get(day['date'], (0, 0))[0])
        company = _hours_from_seconds(split.get(day['date'], (0, 0))[1])
        allocatable = physical + remote
        errand = company
        worked = physical + remote + company
        manual = sum(
            (row['cells'][day['day'] - 1]['value'] for row in project_rows),
            Decimal('0'),
        ) + other_cells[day['day'] - 1]['value']
        allocated = manual + errand
        total_worked += worked
        total_allocatable += allocatable
        total_errand += errand
        total_manual += manual
        total_allocated += allocated
        day_rows.append({
            **day,
            'worked': worked,
            'allocatable': allocatable,
            'errand': errand,
            'manual_allocated': manual,
            'allocated': allocated,
            'remaining': worked - allocated,
            'over': manual > allocatable + TOLERANCE,
        })

    return {
        'days': day_rows,
        'projects': project_rows,
        'other_cells': other_cells,
        'other_title': other_title,
        'other_total': other_total,
        'errand_cells': errand_cells,
        'errand_total': errand_total,
        'errand_label': errand_label,
        'errand_destinations': errand_destinations,
        'total_worked': total_worked,
        'total_allocatable': total_allocatable,
        'total_errand': total_errand,
        'total_manual': total_manual,
        'total_allocated': total_allocated,
        'total_remaining': total_worked - total_allocated,
    }


def parse_hours(raw):
    """ورودی ساعت را به Decimal تبدیل می‌کند (ارقام فارسی هم پذیرفته می‌شود)."""
    if raw is None:
        return Decimal('0.00'), None
    text = str(raw).strip()
    if not text:
        return Decimal('0.00'), None
    text = text.translate(str.maketrans(
        '۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩', '01234567890123456789',
    ))
    text = text.replace('٫', '.').replace(',', '.').replace(' ', '')
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None, 'عدد معتبر نیست'
    if value < 0:
        return None, 'ساعت نمی‌تواند منفی باشد'
    if value > MAX_DAILY_HOURS:
        return None, 'بیش از ۲۴ ساعت مجاز نیست'
    return quantize(value), None


def save_allocations(user, year, month, projects, data):
    """ذخیرهٔ کل ماتریس یک ماه.

    خروجی: (موفق؟، فهرست خطاها)

    هیچ روزی نمی‌تواند بیشتر از ساعت کار ثبت‌شده در سیستم حضور و غیاب
    تخصیص بگیرد. ردیف «سایر» بدون عنوان مجاز نیست.
    """
    start, end, _count = month_bounds(year, month)
    days = month_days(year, month)
    attendance = attendance_map(user, year, month)
    split = errand_split_map(user, year, month, attendance)
    projects = list(projects)
    other_title = (data.get('other_title') or '').strip()[:120]

    errors = []
    parsed = {}
    notes = {}

    for day in days:
        values = {}
        day_notes = {}
        day_total = Decimal('0')
        # حضور فیزیکی + مأموریت ریموت قابل تخصیص است؛ مأموریت شرکتی خودکار.
        allowed = allowed_hours_for(attendance, split, day['date'])

        for project in projects:
            raw = data.get('p%d_%d' % (project.id, day['day']))
            value, error = parse_hours(raw)
            if error:
                errors.append('%s — %s: %s' % (project.name, day['jalali'], error))
                value = Decimal('0')
            values[project.id] = value
            day_notes[project.id] = (
                data.get('note_p%d_%d' % (project.id, day['day'])) or ''
            ).strip()[:300]
            day_total += value

        other_value, error = parse_hours(data.get('other_%d' % day['day']))
        if error:
            errors.append('سایر — %s: %s' % (day['jalali'], error))
            other_value = Decimal('0')
        values['other'] = other_value
        day_notes['other'] = (
            data.get('note_other_%d' % day['day']) or ''
        ).strip()[:300]
        day_total += other_value

        if allowed == 0 and day_total > 0:
            errors.append(
                '%s: ساعت کار قابل تخصیص (بدون مأموریت) ثبت نشده است؛ '
                'امکان تخصیص وجود ندارد.'
                % day['jalali']
            )
        elif day_total > allowed + TOLERANCE:
            errors.append(
                '%s: جمع تخصیص (%s) از ساعت کار قابل تخصیص بدون مأموریت '
                '(%s) بیشتر است.'
                % (day['jalali'], day_total, allowed)
            )
        if other_value > 0 and not other_title:
            errors.append(
                '%s: برای ردیف «سایر» عنوان فعالیت را وارد کنید.'
                % day['jalali']
            )
        parsed[day['date']] = values
        notes[day['date']] = day_notes

    if errors:
        return False, errors

    with transaction.atomic():
        WorkLog.objects.filter(
            user=user, date__gte=start, date__lte=end,
        ).delete()
        objects = []
        for date, values in parsed.items():
            for project in projects:
                value = values.get(project.id, Decimal('0'))
                if value > 0:
                    objects.append(WorkLog(
                        user=user, date=date, project=project, hours=value,
                        note=notes.get(date, {}).get(project.id, ''),
                    ))
            other_value = values.get('other', Decimal('0'))
            if other_value > 0:
                objects.append(WorkLog(
                    user=user, date=date, project=None,
                    other_title=other_title, hours=other_value,
                    note=notes.get(date, {}).get('other', ''),
                ))
        if objects:
            WorkLog.objects.bulk_create(objects)

        WorkLogMonth.objects.get_or_create(
            user=user, year=year, month=month,
            defaults={'status': WorkLogMonth.STATUS_DRAFT},
        )
    return True, []


def month_totals(user, year, month):
    matrix = month_matrix(user, year, month)
    return {
        'worked': matrix['total_worked'],
        'allocated': matrix['total_allocated'],
        'remaining': matrix['total_remaining'],
    }


def report_data(user, year, month):
    """دادهٔ گزارش ماهانه برای نمایش/اکسل (۳۱ ستون روز)."""
    start, end, days_in_month = month_bounds(year, month)
    logs = (
        WorkLog.objects
        .filter(user=user, date__gte=start, date__lte=end)
        .select_related('project')
    )

    attendance = attendance_map(user, year, month)
    split = errand_split_map(user, year, month, attendance)

    groups = {}
    for log in logs:
        jd = jdatetime.date.fromgregorian(date=log.date)
        day = jd.day
        if log.project_id:
            key = ('p', log.project_id)
            title = log.project.name
            order = (0, log.project.sort_order, log.project.name)
        else:
            title = (log.other_title or 'سایر').strip() or 'سایر'
            key = ('o', title)
            order = (1, 999, title)
        group = groups.setdefault(key, {
            'title': title, 'values': {}, 'order': order,
        })
        group['values'][day] = (
            group['values'].get(day, Decimal('0')) + log.hours
        )

    project_rows = []
    other_rows = []
    for group in sorted(groups.values(), key=lambda item: item['order']):
        values = [
            group['values'].get(day, Decimal('0'))
            for day in range(1, 32)
        ]
        row = {
            'title': group['title'],
            'values': values,
            'total': sum(values, Decimal('0')),
        }
        if group['order'][0] == 0:
            project_rows.append(row)
        else:
            other_rows.append(row)

    # ردیف‌های مأموریت شرکتی تأییدشده — به تفکیک مقصد (خودکار).
    # در وب یک ردیف جمع می‌ماند تا جدول شلوغ نشود، ولی اکسل مقصد هر
    # مأموریت را جدا نشان می‌دهد.
    company_map = company_errand_map(user, year, month, attendance)
    by_destination = {}
    for date, per_destination in company_map.items():
        day = jdatetime.date.fromgregorian(date=date).day
        for destination, seconds in per_destination.items():
            values = by_destination.setdefault(
                destination, [Decimal('0')] * 31,
            )
            values[day - 1] += _hours_from_seconds(seconds)

    rows = list(project_rows)
    if by_destination:
        for destination in sorted(by_destination):
            values = by_destination[destination]
            title = (
                'مأموریت شرکتی — %s' % destination
                if destination != 'بدون مقصد'
                else 'مأموریت شرکتی (بدون مقصد)'
            )
            rows.append({
                'title': title,
                'values': values,
                'total': sum(values, Decimal('0')),
                'auto': True,
            })
    else:
        rows.append({
            'title': 'مأموریت شرکتی (تأییدشده)',
            'values': [Decimal('0')] * 31,
            'total': Decimal('0'),
            'auto': True,
        })
    rows.extend(other_rows)

    total_by_day = []
    for day in range(1, 32):
        total_by_day.append(sum(
            (row['values'][day - 1] for row in rows), Decimal('0'),
        ))
    grand_total = sum((row['total'] for row in rows), Decimal('0'))
    errand_total = sum(
        (row['total'] for row in rows if row.get('auto')),
        Decimal('0'),
    )

    return {
        'user': user,
        'year': year,
        'month': month,
        'month_label': month_label(year, month),
        'days_in_month': days_in_month,
        'rows': rows,
        'total_by_day': total_by_day,
        'grand_total': grand_total,
        'errand_total': errand_total,
        'report_date': jdatetime.date.fromgregorian(
            date=timezone.localdate(),
        ).strftime('%Y/%m/%d'),
    }


def admin_month_rows(year, month):
    """فهرست همهٔ کارمندان فعال با وضعیت و جمع ساعت‌هایشان."""
    rows = []
    for user in User.objects.filter(is_active=True).order_by('username'):
        month_state = WorkLogMonth.objects.filter(
            user=user, year=year, month=month,
        ).first()
        totals = month_totals(user, year, month)
        rows.append({
            'user': user,
            'display_name': user_display(user),
            'month_state': month_state,
            'totals': totals,
        })
    return rows


def projects_for_month(user, year, month):
    """پروژه‌های فعال + پروژه‌های غیرفعالی که در همین ماه رکورد دارند."""
    start, end, _count = month_bounds(year, month)
    used_ids = (
        Project.objects
        .filter(
            worklogs__user=user,
            worklogs__date__gte=start,
            worklogs__date__lte=end,
        )
        .values_list('pk', flat=True)
    )
    return list(
        Project.objects
        .filter(models.Q(is_active=True) | models.Q(pk__in=used_ids))
        .distinct()
        .order_by('sort_order', 'name')
    )


def overlay_matrix(matrix, projects, data):
    """مقادیر ارسال‌شدهٔ فرم را روی ماتریس می‌گذارد تا بعد از خطا از دست نروند."""
    projects = list(projects)
    for row in matrix['projects']:
        total = Decimal('0')
        for cell in row['cells']:
            value, _error = parse_hours(
                data.get('p%d_%d' % (row['project'].id, cell['day']))
            )
            if value is None:
                value = Decimal('0')
            cell['value'] = value
            cell['note'] = (
                data.get('note_p%d_%d' % (row['project'].id, cell['day'])) or ''
            ).strip()[:300]
            total += value
        row['total'] = total

    matrix['other_title'] = (data.get('other_title') or '').strip()[:120]
    other_total = Decimal('0')
    for cell in matrix['other_cells']:
        value, _error = parse_hours(data.get('other_%d' % cell['day']))
        if value is None:
            value = Decimal('0')
        cell['value'] = value
        cell['note'] = (
            data.get('note_other_%d' % cell['day']) or ''
        ).strip()[:300]
        other_total += value
    matrix['other_total'] = other_total

    total_worked = Decimal('0')
    total_allocatable = Decimal('0')
    total_errand = Decimal('0')
    total_manual = Decimal('0')
    total_allocated = Decimal('0')
    for index, day in enumerate(matrix['days']):
        manual = sum(
            (row['cells'][index]['value'] for row in matrix['projects']),
            Decimal('0'),
        ) + matrix['other_cells'][index]['value']
        errand = day.get('errand', Decimal('0'))
        allocated = manual + errand
        day['manual_allocated'] = manual
        day['allocated'] = allocated
        day['remaining'] = day['worked'] - allocated
        day['over'] = manual > day.get('allocatable', Decimal('0')) + TOLERANCE
        total_worked += day['worked']
        total_allocatable += day.get('allocatable', Decimal('0'))
        total_errand += errand
        total_manual += manual
        total_allocated += allocated

    matrix['total_worked'] = total_worked
    matrix['total_allocatable'] = total_allocatable
    matrix['total_errand'] = total_errand
    matrix['total_manual'] = total_manual
    matrix['total_allocated'] = total_allocated
    matrix['total_remaining'] = total_worked - total_allocated
    return matrix


# ============================================================
# گزارش جمعی پروژه‌ها برای ادمین
# ============================================================

def admin_project_summary(year, month):
    """جمع ساعت هر پروژه به تفکیک کارمند در یک ماه.

    خروجی برای اکسل «جمع پروژه‌ها»:
      users        فهرست کارمندانی که در این ماه کارکرد دارند
      projects     ردیف‌های پروژه با total و by_user
      total_by_user جمع هر کارمند
      grand_total  جمع کل ماه
    """
    start, end, _count = month_bounds(year, month)
    logs = (
        WorkLog.objects
        .filter(date__gte=start, date__lte=end)
        .select_related('project', 'user')
    )

    users_by_id = {}
    project_user = defaultdict(lambda: defaultdict(Decimal))
    other_user = defaultdict(Decimal)

    for log in logs:
        users_by_id[log.user_id] = log.user
        if log.project_id:
            project_user[log.project_id][log.user_id] += log.hours
        else:
            other_user[log.user_id] += log.hours

    users = sorted(
        users_by_id.values(),
        key=lambda user: (user_display(user), user.username),
    )

    projects = (
        Project.objects
        .filter(pk__in=set(project_user))
        .order_by('sort_order', 'name')
    )
    rows = []
    for project in projects:
        by_user = project_user.get(project.pk, {})
        rows.append({
            'name': project.name,
            'by_user': by_user,
            'total': sum(by_user.values(), Decimal('0')),
        })

    if other_user:
        rows.append({
            'name': 'سایر',
            'by_user': other_user,
            'total': sum(other_user.values(), Decimal('0')),
        })

    total_by_user = {
        user.pk: sum(
            (row['by_user'].get(user.pk, Decimal('0')) for row in rows),
            Decimal('0'),
        )
        for user in users
    }
    grand_total = sum((row['total'] for row in rows), Decimal('0'))

    return {
        'year': year,
        'month': month,
        'month_label': month_label(year, month),
        'users': users,
        'projects': rows,
        'total_by_user': total_by_user,
        'grand_total': grand_total,
    }


def admin_project_year(year):
    """جمع ساعت هر پروژه در ۱۲ ماه یک سال شمسی (همهٔ کارمندان)."""
    start = jdatetime.date(year, 1, 1).togregorian()
    _start, end, _count = month_bounds(year, 12)
    logs = (
        WorkLog.objects
        .filter(date__gte=start, date__lte=end)
        .select_related('project')
    )

    monthly = defaultdict(lambda: [Decimal('0')] * 12)
    other_values = [Decimal('0')] * 12

    for log in logs:
        month_index = jdatetime.date.fromgregorian(date=log.date).month - 1
        if log.project_id:
            monthly[log.project_id][month_index] += log.hours
        else:
            other_values[month_index] += log.hours

    projects = (
        Project.objects
        .filter(pk__in=set(monthly))
        .order_by('sort_order', 'name')
    )
    rows = []
    for project in projects:
        values = monthly.get(project.pk, [Decimal('0')] * 12)
        rows.append({
            'name': project.name,
            'values': values,
            'total': sum(values, Decimal('0')),
        })

    if any(other_values):
        rows.append({
            'name': 'سایر',
            'values': other_values,
            'total': sum(other_values, Decimal('0')),
        })

    return {
        'year': year,
        'months': MONTHS_FA,
        'projects': rows,
        'total_by_month': [
            sum((row['values'][index] for row in rows), Decimal('0'))
            for index in range(12)
        ],
        'grand_total': sum((row['total'] for row in rows), Decimal('0')),
    }
