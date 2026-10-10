"""فیلترهای کمکی قالب‌های پنل ادمین."""
import datetime as dt

from django import template

register = template.Library()

_FA = str.maketrans('0123456789', '۰۱۲۳۴۵۶۷۸۹')


@register.filter
def fa(value):
    """تبدیل ارقام لاتین به فارسی."""
    if value is None:
        return ''
    if isinstance(value, float) and value == int(value):
        value = int(value)
    return str(value).translate(_FA)


@register.filter
def get(d, key):
    """گرفتن مقدار از dict در قالب: {{ dashboard.counts|get:'users' }}"""
    try:
        return d.get(key)
    except AttributeError:
        return None


@register.filter
def jalali_date(value):
    """تاریخ میلادی/شمسی جنگو → رشتهٔ شمسی خوانا."""
    if not value:
        return '—'
    if isinstance(value, dt.datetime):
        try:
            import jdatetime
            return jdatetime.datetime.fromgregorian(datetime=value).strftime('%Y/%m/%d')
        except Exception:
            value = value.date()
    if isinstance(value, dt.date):
        try:
            import jdatetime
            return jdatetime.date.fromgregorian(date=value).strftime('%Y/%m/%d')
        except Exception:
            return value.isoformat()
    return str(value)


@register.filter
def clock_fa(value):
    """ساعت یک datetime به شکل HH:MM با ارقام فارسی."""
    if not value:
        return '—'
    try:
        text = value.strftime('%H:%M')
    except AttributeError:
        return str(value)
    return text.translate(_FA)


@register.simple_tag
def kpi(dashboard, key):
    """یک نشانگر را با کلیدش پیدا می‌کند."""
    for item in dashboard.get('kpis', []):
        if item['key'] == key:
            return item
    return {}
