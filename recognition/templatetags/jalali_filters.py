from django import template
import jdatetime
from django.utils import timezone

register = template.Library()

PERSIAN_MONTHS = [
    'فروردین', 'اردیبهشت', 'خرداد', 'تیر', 'مرداد', 'شهریور',
    'مهر', 'آبان', 'آذر', 'دی', 'بهمن', 'اسفند'
]

RLM = '\u200f'

def _to_tehran(value):
    """Convert datetime to Tehran timezone if it's timezone-aware."""
    if value is None:
        return None
    # Handle date objects (no timezone)
    if not hasattr(value, 'utcoffset'):
        return value
    if timezone.is_aware(value):
        return timezone.localtime(value)
    return value

@register.filter
def to_jalali(value):
    if not value:
        return '-'
    try:
        value = _to_tehran(value)
        if hasattr(value, 'hour'):
            j = jdatetime.datetime.fromgregorian(datetime=value)
            result = "%s%d %s %d - %02d:%02d:%02d" % (
                RLM, j.day, PERSIAN_MONTHS[j.month-1], j.year, j.hour, j.minute, j.second
            )
        else:
            j = jdatetime.date.fromgregorian(date=value)
            result = "%s%d %s %d" % (RLM, j.day, PERSIAN_MONTHS[j.month-1], j.year)
        return result
    except Exception:
        return str(value)


@register.filter
def to_jalali_full(value):
    """Full Jalali date including Persian weekday, e.g. دوشنبه ۲۵ مهر ۱۴۰۳."""
    if not value:
        return '-'
    try:
        value = _to_tehran(value)
        j = jdatetime.date.fromgregorian(date=value)
        # Persian weekdays (jdatetime weekday: 0=Saturday, 6=Friday)
        jalali_weekdays = ['شنبه', 'یکشنبه', 'دوشنبه', 'سه‌شنبه', 'چهارشنبه', 'پنج‌شنبه', 'جمعه']
        weekday = jalali_weekdays[j.weekday()]
        return "%s %d %s %d" % (weekday, j.day, PERSIAN_MONTHS[j.month - 1], j.year)
    except Exception:
        return str(value)


_FA_TRANS = str.maketrans('0123456789', '۰۱۲۳۴۵۶۷۸۹')


@register.filter
def fa(value):
    return str(value).translate(_FA_TRANS)


@register.filter
def clock(value):
    if not value:
        return '-'
    try:
        value = _to_tehran(value)
        return "%02d:%02d" % (value.hour, value.minute)
    except Exception:
        return str(value)


@register.filter
def ja_datetime(value):
    """Jalali numeric datetime in Persian digits, e.g. ۱۴۰۵/۰۶/۲۶ ۰۳:۲۹."""
    if not value:
        return ''
    try:
        value = _to_tehran(value)
        j = jdatetime.datetime.fromgregorian(datetime=value)
        raw = '%d/%02d/%02d %02d:%02d' % (j.year, j.month, j.day, j.hour, j.minute)
        return raw.translate(_FA_TRANS)
    except Exception:
        return str(value)
