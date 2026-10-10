"""خواندن فایل لاگ `django.log` بدون نیاز به SSH/دستور `tail`."""
import datetime as dt
import os
import re

from django.conf import settings

LEVELS = ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL')
MAX_LINES = 2000


def log_path():
    return os.path.join(settings.BASE_DIR, 'django.log')


def _human(n):
    for unit in ('بایت', 'کیلوبایت', 'مگابایت', 'گیگابایت'):
        if n < 1024 or unit == 'گیگابایت':
            return '%s %s' % (int(n) if unit == 'بایت' else '%.1f' % n, unit)
        n /= 1024.0


def info():
    path = log_path()
    if not os.path.isfile(path):
        return {'exists': False, 'size_fa': '—', 'mtime': None}
    st = os.stat(path)
    return {
        'exists': True,
        'size_fa': _human(st.st_size),
        'bytes': st.st_size,
        'mtime': dt.datetime.fromtimestamp(st.st_mtime),
        'big': st.st_size > 5 * 1024 * 1024,
    }


def tail(lines=400, level=''):
    """آخرین `lines` خط لاگ؛ با فیلتر سطح هشدار (اختیاری)."""
    path = log_path()
    if not os.path.isfile(path):
        return []
    lines = max(20, min(int(lines or 400), MAX_LINES))
    try:
        with open(path, 'rb') as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            block = 8192
            data = b''
            # از انتها می‌خوانیم تا لازم نباشد کل فایل در حافظه بیاید
            while size > 0 and data.count(b'\n') <= lines:
                step = min(block, size)
                size -= step
                fh.seek(size)
                data = fh.read(step) + data
                if size == 0:
                    break
    except OSError:
        return []

    text = data.decode('utf-8', errors='replace')
    all_lines = text.splitlines()
    if size > 0 and all_lines:
        all_lines = all_lines[1:]   # خط ناقص اولِ بافر حذف می‌شود
    out = all_lines[-lines:]

    if level:
        idx = LEVELS.index(level) if level in LEVELS else None
        if idx is not None:
            allowed = LEVELS[idx:]
            out = [l for l in out
                   if any((' %s ' % lv) in l or l.startswith(lv)
                          or ('%s ' % lv) in l for lv in allowed)]
    return out


def clear():
    path = log_path()
    if os.path.isfile(path):
        open(path, 'w').close()
        return True
    return False


def stats(lines):
    """شمارش سطوح در همان برش خوانده‌شده."""
    counts = {lv: 0 for lv in LEVELS}
    for line in lines:
        for lv in LEVELS:
            if re.search(r'\b%s\b' % lv, line):
                counts[lv] += 1
                break
    return counts
