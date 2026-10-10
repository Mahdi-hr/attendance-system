"""ابزارهای عمومی پنل ادمین."""
import re
import secrets
import string
import unicodedata

# حروف نامفهوم حذف شدند تا رمز راحت تایپ/کپی شود
_PW_ALPHABET = ''.join(
    c for c in (string.ascii_letters + string.digits) if c not in 'O0Il1')

# همان مجموعه‌ای که جنگو برای نام کاربری مجاز می‌داند.
_USERNAME_RE = re.compile(r'[^\w.@+-]', re.UNICODE)


def get_valid_username(raw):
    """نام کاربریِ ورودی را به شکلی مجاز و بدون تکرارِ متن درمی‌آورد.

    مشابه تابعی که قبلاً در `django.utils.text` بود (در Django 5.0 حذف
    شده). نویسه‌های غیرمجاز حذف/جایگزین می‌شوند؛ اگر نتیجه خالی شد
    `user` برمی‌گردد. تکراری‌نبودن را خودِ فراخواننده چک می‌کند.
    """
    if raw is None:
        raw = ''
    # نرمال‌سازی یونیکد + حذف فاصله‌های انتهایی
    value = unicodedata.normalize('NFKC', str(raw)).strip()
    value = value.replace(' ', '_')
    value = _USERNAME_RE.sub('', value)
    value = value.strip('._-')
    if not value:
        value = 'user'
    return value[:150]


def random_password(length=12):
    """تولید رمز تصادفیِ خوانا.

    نکته: متد `BaseUserManager.make_random_password` در Django 5.0 حذف
    شده؛ بنابراین اینجا خودمان پیاده می‌کنیم.
    """
    length = max(int(length), 8)
    while True:
        pw = ''.join(secrets.choice(_PW_ALPHABET) for _ in range(length))
        if (any(c.islower() for c in pw)
                and any(c.isupper() for c in pw)
                and any(c.isdigit() for c in pw)):
            return pw
