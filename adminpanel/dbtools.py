"""ابزار پایگاه داده: پشتیبان‌گیری، بازیابی و کاوشگر SQL.

همهٔ کارها از داخل مرورگر انجام می‌شود؛ نیازی به دسترسی مستقیم به
`db.sqlite3` یا نصب ابزار جانبی نیست.
"""
import datetime as dt
import os
import re
import sqlite3
import time

from django.conf import settings
from django.db import connection, transaction

# ============================================================
# مسیرها
# ============================================================
def db_path():
    name = settings.DATABASES['default']['NAME']
    if not name or str(name).startswith(':memory:'):
        return None
    return str(name)


def is_sqlite():
    return 'sqlite' in settings.DATABASES['default'].get('ENGINE', '')


def backup_dir():
    path = os.path.join(settings.BASE_DIR, 'backups', 'admin_panel')
    os.makedirs(path, exist_ok=True)
    return path


def _safe_name(name):
    """فقط اسم فایل ساده مجاز است (محافظت در برابر path traversal)."""
    if not name or '/' in name or '\\' in name or '..' in name:
        return None
    if not re.fullmatch(r'[\w.\-]+', name):
        return None
    return name


# ============================================================
# پشتیبان‌گیری / بازیابی
# ============================================================
def create_backup(label=''):
    """یک نسخهٔ سازگار با SQLite می‌سازد (حتی وقتی سرویس در حال اجراست)."""
    src = db_path()
    if not src or not os.path.isfile(src):
        return None, 'دیتابیس SQLite پیدا نشد.'
    stamp = dt.datetime.now().strftime('%Y%m%d-%H%M%S')
    suffix = re.sub(r'[^\w\-]', '-', label)[:24] if label else ''
    filename = 'backup-%s%s.db' % (stamp, ('-' + suffix) if suffix else '')
    dest = os.path.join(backup_dir(), filename)
    try:
        source = sqlite3.connect('file:%s?mode=ro' % src, uri=True)
        target = sqlite3.connect(dest)
        with target:
            source.backup(target)
        target.close()
        source.close()
        os.chmod(dest, 0o640)
        return filename, 'نسخهٔ پشتیبان ساخته شد.'
    except Exception as exc:
        if os.path.isfile(dest):
            try:
                os.remove(dest)
            except OSError:
                pass
        return None, 'خطا در پشتیبان‌گیری: %s' % exc


def list_backups():
    out = []
    root = backup_dir()
    try:
        names = os.listdir(root)
    except OSError:
        return out
    for name in names:
        if not _safe_name(name):
            continue
        full = os.path.join(root, name)
        if not os.path.isfile(full):
            continue
        st = os.stat(full)
        out.append({
            'name': name,
            'size': st.st_size,
            'size_fa': _human(st.st_size),
            'mtime': dt.datetime.fromtimestamp(st.st_mtime),
        })
    out.sort(key=lambda x: x['mtime'], reverse=True)
    return out


def delete_backup(name):
    name = _safe_name(name)
    if not name:
        return False, 'نام فایل نامعتبر است.'
    full = os.path.join(backup_dir(), name)
    if not os.path.isfile(full):
        return False, 'فایل پشتیبان پیدا نشد.'
    try:
        os.remove(full)
        return True, 'حذف شد.'
    except OSError as exc:
        return False, 'خطا در حذف: %s' % exc


def open_backup(name):
    """مسیر امن فایل پشتیبان یا None."""
    name = _safe_name(name)
    if not name:
        return None
    full = os.path.join(backup_dir(), name)
    return full if os.path.isfile(full) else None


def restore_backup(name):
    """محتوای یک پشتیبان را روی دیتابیس فعلی می‌نشاند.

    به‌جای جایگزینی فایل (که اتصال‌های باز را می‌شکند) از API backup
    خود SQLite استفاده می‌شود تا همهٔ اتصال‌ها دیتابیس به‌روز ببینند.
    """
    src_path = open_backup(name)
    if not src_path:
        return False, 'فایل پشتیبان پیدا نشد.'
    dst = db_path()
    if not dst or not is_sqlite():
        return False, 'فقط برای پایگاه داده SQLite قابل اجراست.'
    try:
        connection.close()
        source = sqlite3.connect('file:%s?mode=ro' % src_path, uri=True)
        target = sqlite3.connect(dst)
        with target:
            source.backup(target)
        target.close()
        source.close()
        return True, 'بازیابی انجام شد. اگر صفحه‌ها عجیب شدند، سرور را یک‌بار ری‌استارت کنید.'
    except Exception as exc:
        return False, 'خطا در بازیابی: %s' % exc


# ============================================================
# کاوشگر SQL
# ============================================================
# کلیدواژه‌هایی که هرگز اجازهٔ اجرا ندارند
FORBIDDEN = re.compile(
    r'\b(ATTACH|DETACH|VACUUM\s+INTO|PRAGMA\s+writable_schema|'
    r'LOAD_EXTENSION|ATTACH\s+DATABASE)\b',
    re.IGNORECASE,
)

# دستوراتی که فقط در حالت «نوشتن» مجاز‌اند
WRITE_KEYWORDS = re.compile(
    r'^\s*(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|REPLACE|TRUNCATE|'
    r'REINDEX|REINDEX)\b',
    re.IGNORECASE,
)

READ_KEYWORDS = re.compile(
    r'^\s*(SELECT|WITH|PRAGMA|EXPLAIN|SHOW|VALUES)\b',
    re.IGNORECASE,
)

MAX_ROWS = 500


def _human(n):
    for unit in ('B', 'KB', 'MB', 'GB'):
        if n < 1024 or unit == 'GB':
            return ('%s %s' % (('%.1f' % n) if unit != 'B' else int(n), unit))
        n /= 1024.0


def classify(sql):
    """'read' | 'write' | 'forbidden'"""
    if not sql or not sql.strip():
        return 'empty'
    if FORBIDDEN.search(sql):
        return 'forbidden'
    if WRITE_KEYWORDS.search(sql):
        return 'write'
    if READ_KEYWORDS.search(sql):
        return 'read'
    return 'write'   # هر چیز ناشناخته، نوشتنی حساب می‌شود


def run_sql(sql, write=False, limit=MAX_ROWS):
    """SQL را اجرا می‌کند.

    در حالت پیش‌فرض فقط‌خواندنی است؛ نوشتن نیاز به `write=True` دارد که
    در ویو با تأیید صریح مدیر گرفته می‌شود.
    """
    kind = classify(sql)
    if kind == 'empty':
        return {'ok': False, 'error': 'دستور خالی است.'}
    if kind == 'forbidden':
        return {'ok': False, 'error': 'این دستور به دلایل امنیتی مسدود است.'}
    if kind == 'write' and not write:
        return {'ok': False,
                'error': 'این دستور «نوشتنی» است. برای اجرای آن باید حالت '
                         '«اجازهٔ تغییر» را روشن کنید و عبارت تأیید را بنویسید.'}

    started = time.perf_counter()
    try:
        with connection.cursor() as cursor:
            if kind == 'write':
                try:
                    with transaction.atomic():
                        cursor.execute(sql)
                        affected = cursor.rowcount
                        columns, rows = _fetch(cursor, limit)
                except Exception:
                    # هیچ تغییری نباید نیمه‌کاره بماند
                    raise
            else:
                cursor.execute(sql)
                affected = cursor.rowcount
                columns, rows = _fetch(cursor, limit)
    except Exception as exc:
        return {'ok': False, 'error': str(exc).strip() or repr(exc)}

    elapsed = (time.perf_counter() - started) * 1000
    return {
        'ok': True,
        'columns': columns,
        'rows': rows,
        'row_count': len(rows),
        'affected': affected if kind == 'write' else None,
        'truncated': len(rows) >= limit,
        'ms': round(elapsed, 2),
        'kind': kind,
    }


def _fetch(cursor, limit):
    description = cursor.description
    if description is None:
        return [], []
    columns = [d[0] for d in description]
    rows = []
    for raw in cursor.fetchmany(limit):
        rows.append([_cell(v) for v in raw])
    return columns, rows


def _cell(value):
    if value is None:
        return '—'
    if isinstance(value, bytes):
        return '<b>blob (%s بایت)</b>' % len(value)
    if isinstance(value, (int, float)):
        return value
    text = str(value)
    return text if len(text) <= 500 else text[:500] + '…'


def table_stats():
    """فهرست جدول‌ها + تعداد ردیف + ایندکس‌ها (برای نوار کناری SQL)."""
    if not is_sqlite():
        return []
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name")
            names = [r[0] for r in cursor.fetchall()]
            out = []
            for name in names:
                if not re.fullmatch(r'[\w]+', name):
                    continue
                cursor.execute('SELECT COUNT(*) FROM "%s"' % name)
                count = cursor.fetchone()[0]
                cursor.execute("SELECT COUNT(*) FROM sqlite_master "
                               "WHERE type='index' AND tbl_name=?", [name])
                idx = cursor.fetchone()[0]
                out.append({'name': name, 'count': count, 'indexes': idx})
            return out
    except Exception:
        return []


def preview_table(name, limit=100):
    if not name or not re.fullmatch(r'[\w]+', name):
        return {'ok': False, 'error': 'نام جدول نامعتبر است.'}
    return run_sql('SELECT * FROM "%s" LIMIT %d' % (name, limit))
