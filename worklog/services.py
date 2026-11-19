"""منطق ثبت کارکرد پروژه‌ها و ساخت دادهٔ گزارش/اکسل."""

import datetime as dt
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

import jdatetime
from django.contrib.auth import get_user_model
from django.db import models, transaction
from django.utils import timezone

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


def _hours_from_seconds(seconds):
    return quantize(Decimal(seconds or 0) / Decimal(3600))


def worked_hours_for(attendance, date):
    row = attendance.get(date)
    if not row:
        return Decimal('0.00')
    return _hours_from_seconds(row.get('total_seconds'))


def month_matrix(user, year, month, projects=None):
    """دادهٔ ماتریس پروژه × روز برای نمایش و ویرایش."""
    start, end, _count = month_bounds(year, month)
    days = month_days(year, month)
    attendance = attendance_map(user, year, month)

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
            by_project[(log.project_id, log.date)] = log.hours
        else:
            other[log.date] = log.hours
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
            value = by_project.get((project.id, d['date']), Decimal('0'))
            total += value
            cells.append({'day': d['day'], 'value': value})
        project_rows.append({
            'project': project,
            'cells': cells,
            'total': total,
        })

    other_cells = []
    other_total = Decimal('0')
    for d in days:
        value = other.get(d['date'], Decimal('0'))
        other_total += value
        other_cells.append({'day': d['day'], 'value': value})

    day_rows = []
    total_worked = Decimal('0')
    total_allocated = Decimal('0')
    for day in days:
        worked = worked_hours_for(attendance, day['date'])
        allocated = sum(
            (row['cells'][day['day'] - 1]['value'] for row in project_rows),
            Decimal('0'),
        ) + other_cells[day['day'] - 1]['value']
        total_worked += worked
        total_allocated += allocated
        day_rows.append({
            **day,
            'worked': worked,
            'allocated': allocated,
            'remaining': worked - allocated,
            'over': allocated > worked + TOLERANCE,
        })

    return {
        'days': day_rows,
        'projects': project_rows,
        'other_cells': other_cells,
        'other_title': other_title,
        'other_total': other_total,
        'total_worked': total_worked,
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
    projects = list(projects)
    other_title = (data.get('other_title') or '').strip()[:120]

    errors = []
    parsed = {}

    for day in days:
        values = {}
        day_total = Decimal('0')
        worked = worked_hours_for(attendance, day['date'])

        for project in projects:
            raw = data.get('p%d_%d' % (project.id, day['day']))
            value, error = parse_hours(raw)
            if error:
                errors.append('%s — %s: %s' % (project.name, day['jalali'], error))
                value = Decimal('0')
            values[project.id] = value
            day_total += value

        other_value, error = parse_hours(data.get('other_%d' % day['day']))
        if error:
            errors.append('سایر — %s: %s' % (day['jalali'], error))
            other_value = Decimal('0')
        values['other'] = other_value
        day_total += other_value

        if worked == 0 and day_total > 0:
            errors.append(
                '%s: ساعت کار سیستمی ثبت نشده است؛ امکان تخصیص وجود ندارد.'
                % day['jalali']
            )
        elif day_total > worked + TOLERANCE:
            errors.append(
                '%s: جمع تخصیص (%s) از ساعت کار سیستم (%s) بیشتر است.'
                % (day['jalali'], day_total, worked)
            )
        if other_value > 0 and not other_title:
            errors.append(
                '%s: برای ردیف «سایر» عنوان فعالیت را وارد کنید.'
                % day['jalali']
            )
        parsed[day['date']] = values

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
                    ))
            other_value = values.get('other', Decimal('0'))
            if other_value > 0:
                objects.append(WorkLog(
                    user=user, date=date, project=None,
                    other_title=other_title, hours=other_value,
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

    rows = []
    for group in sorted(groups.values(), key=lambda item: item['order']):
        values = [
            group['values'].get(day, Decimal('0'))
            for day in range(1, 32)
        ]
        rows.append({
            'title': group['title'],
            'values': values,
            'total': sum(values, Decimal('0')),
        })

    total_by_day = []
    for day in range(1, 32):
        total_by_day.append(sum(
            (row['values'][day - 1] for row in rows), Decimal('0'),
        ))
    grand_total = sum((row['total'] for row in rows), Decimal('0'))

    return {
        'user': user,
        'year': year,
        'month': month,
        'month_label': month_label(year, month),
        'days_in_month': days_in_month,
        'rows': rows,
        'total_by_day': total_by_day,
        'grand_total': grand_total,
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
            total += value
        row['total'] = total

    matrix['other_title'] = (data.get('other_title') or '').strip()[:120]
    other_total = Decimal('0')
    for cell in matrix['other_cells']:
        value, _error = parse_hours(data.get('other_%d' % cell['day']))
        if value is None:
            value = Decimal('0')
        cell['value'] = value
        other_total += value
    matrix['other_total'] = other_total

    total_worked = Decimal('0')
    total_allocated = Decimal('0')
    for index, day in enumerate(matrix['days']):
        allocated = sum(
            (row['cells'][index]['value'] for row in matrix['projects']),
            Decimal('0'),
        ) + matrix['other_cells'][index]['value']
        day['allocated'] = allocated
        day['remaining'] = day['worked'] - allocated
        day['over'] = allocated > day['worked'] + TOLERANCE
        total_worked += day['worked']
        total_allocated += allocated

    matrix['total_worked'] = total_worked
    matrix['total_allocated'] = total_allocated
    matrix['total_remaining'] = total_worked - total_allocated
    return matrix
