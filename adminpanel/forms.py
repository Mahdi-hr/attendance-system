"""فرم‌های ابزارهای پنل ادمین."""
import re

from django import forms
from django.utils import timezone

# ============================================================
# کاوشگر SQL
# ============================================================
class SqlForm(forms.Form):
    sql = forms.CharField(
        label='دستور SQL',
        widget=forms.Textarea(attrs={
            'rows': 8,
            'dir': 'ltr',
            'class': 'form-control font-monospace',
            'placeholder': 'SELECT username, last_login FROM auth_user ORDER BY last_login DESC;',
            'autofocus': True,
        }),
        error_messages={'required': 'دستوری بنویسید.'},
    )
    write = forms.BooleanField(
        label='اجازهٔ تغییر داده (حالت نوشتن)',
        required=False,
    )
    confirm = forms.CharField(
        label='تأیید (بنویسید: تایید)',
        required=False,
        widget=forms.TextInput(attrs={
            'placeholder': 'تایید',
            'class': 'form-control',
            'autocomplete': 'off',
        }),
    )

    def clean(self):
        cleaned = super().clean()
        sql = (cleaned.get('sql') or '').strip().rstrip(';')
        write = bool(cleaned.get('write'))
        confirm = (cleaned.get('confirm') or '').strip()
        if write:
            if confirm != 'تایید':
                raise forms.ValidationError(
                    'برای اجرای دستور نوشتنی باید عبارت «تایید» را در کادر '
                    'تأیید بنویسید.')
        cleaned['sql'] = sql
        cleaned['write'] = write
        return cleaned


# ============================================================
# ایجاد گروهی کاربران
# ============================================================
LINE_RE = re.compile(
    r'^(?P<username>[^,\s]+)'
    r'(?:,(?P<first>[^,]*))?'
    r'(?:,(?P<last>[^,]*))?'
    r'(?:,(?P<email>[^,]*))?'
    r'(?:,(?P<role>[^,]*))?'
    r'(?:,(?P<password>.*))?$'
)


class BulkUserCreateForm(forms.Form):
    lines = forms.CharField(
        label='فهرست کاربران',
        widget=forms.Textarea(attrs={
            'rows': 12,
            'dir': 'ltr',
            'class': 'form-control font-monospace',
            'placeholder': '1001,علی,رضایی,,user,Abc12345\n1002,سارا,محمدی,,both,',
        }),
        help_text=(
            'هر خط: <code dir="ltr">نام_کاربری,نام,خانوادگی,ایمیل,نقش,رمز</code> '
            '— فقط نام کاربری الزامی است. نقش‌ها: <code dir="ltr">user</code>، '
            '<code dir="ltr">admin</code>، <code dir="ltr">both</code>. '
            'رمز خالی گذاشته شود یک رمز تصادفی ساخته می‌شود.'
        ),
    )
    default_role = forms.ChoiceField(
        label='نقش پیش‌فرض',
        choices=[('user', 'کاربر عادی'), ('admin', 'ادمین'),
                 ('both', 'هر دو')],
        initial='user',
        required=False,
    )
    default_password = forms.CharField(
        label='رمز پیش‌فرض',
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control', 'dir': 'ltr',
            'placeholder': 'خالی = رمز تصادفی ۱۲ رقمی',
        }),
    )
    make_staff = forms.BooleanField(
        label='فعال‌سازی «دسترسی پنل مدیریت» (is_staff)',
        required=False,
        initial=True,
    )
    ensure_face = forms.BooleanField(
        label='ساخت شناسهٔ چهره برای هر کاربر تازه',
        required=False,
        initial=True,
    )

    def clean_lines(self):
        raw = self.cleaned_data['lines']
        parsed, errors = [], []
        for i, line in enumerate(raw.splitlines(), start=1):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            m = LINE_RE.match(line)
            if not m:
                errors.append('خط %d نامعتبر است: %s' % (i, line[:60]))
                continue
            data = {k: (v or '').strip() for k, v in m.groupdict().items()}
            if not data['username']:
                errors.append('خط %d: نام کاربری خالی است.' % i)
                continue
            parsed.append(data)
        if errors:
            raise forms.ValidationError(errors)
        if not parsed:
            raise forms.ValidationError('هیچ خطی برای ساخت پیدا نشد.')
        return parsed


# ============================================================
# عملیات گروهی روی کاربران
# ============================================================
class BulkUserOpForm(forms.Form):
    ACTION_CHOICES = [
        ('activate', 'فعال‌سازی حساب‌ها'),
        ('deactivate', 'غیرفعال‌سازی حساب‌ها'),
        ('role_user', 'تغییر نقش به «کاربر عادی»'),
        ('role_admin', 'تغییر نقش به «ادمین»'),
        ('role_both', 'تغییر نقش به «هر دو»'),
        ('make_staff', 'دادن دسترسی پنل مدیریت (is_staff)'),
        ('drop_staff', 'گرفتن دسترسی پنل مدیریت'),
        ('make_superuser', 'تبدیل به superuser (دسترسی کامل)'),
        ('drop_superuser', 'حذف superuser (فقط اگر خودتان نباشید)'),
        ('reset_password', 'ریست رمز عبور و نمایش رمزهای جدید'),
        ('sync_profile', 'همگام‌سازی پروفایل و شناسهٔ چهره'),
        ('force_logout', 'خروج اجباری از همهٔ دستگاه‌ها'),
        ('delete', 'حذف کاربران انتخاب‌شده'),
    ]
    action = forms.ChoiceField(label='عملیات', choices=ACTION_CHOICES)
    role = forms.ChoiceField(
        label='نقش (فقط برای تغییر نقش)',
        choices=[('', '—')] + [(r, r) for r in ('user', 'admin', 'both')],
        required=False,
    )
    password = forms.CharField(
        label='رمز جدید (خالی = تصادفی)',
        required=False,
        widget=forms.TextInput(attrs={'dir': 'ltr', 'class': 'form-control'}),
    )


# ============================================================
# ترمیم حضور و غیاب
# ============================================================
class AttendanceFixForm(forms.Form):
    ACTIONS = [
        ('present_mismatch', 'بازسازی «حضور روزانه» از روی رکوردهای ورود/خروج'),
        ('open_breaks', 'بستن استراحت‌های باز (مدت قابل تنظیم)'),
        ('duplicate_times', 'حذف رکوردهای ورود/خروج تکراری'),
        ('future_times', 'حذف رکوردهای زمان در آینده'),
        ('duplicate_face_uid', 'جداسازی شناسهٔ چهرهٔ تکراری'),
        ('missing_profiles', 'ساخت پروفایل‌های نقش جامانده'),
        ('pending_errors', 'علامت‌گذاری همهٔ خطاهای خروج به‌عنوان رفع‌شده'),
        ('stale_sessions', 'پاک‌سازی نشست‌های منقضی'),
    ]
    date = forms.DateField(
        label='تاریخ (اختیاری برای برخی عملیات)',
        widget=forms.DateInput(attrs={
            'type': 'date', 'class': 'form-control',
        }),
        required=False,
    )
    action = forms.ChoiceField(label='عملیات', choices=ACTIONS)
    minutes = forms.IntegerField(
        label='مدت بستن استراحت (دقیقه)',
        initial=30, min_value=1, max_value=600, required=False,
    )

    def clean_date(self):
        value = self.cleaned_data.get('date')
        return value or timezone.localdate()


class BackupLabelForm(forms.Form):
    label = forms.CharField(
        label='برچسب (اختیاری)',
        required=False,
        max_length=24,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'مثلاً قبل-از-تغییر',
        }),
    )


class RestoreUploadForm(forms.Form):
    file = forms.FileField(
        label='فایل پشتیبان (.db)',
        widget=forms.ClearableFileInput(attrs={'class': 'form-control'}),
    )

    def clean_file(self):
        f = self.cleaned_data['file']
        if f.size > 200 * 1024 * 1024:
            raise forms.ValidationError('فایل بزرگ‌تر از ۲۰۰ مگابایت است.')
        if not f.name.lower().endswith(('.db', '.sqlite', '.sqlite3')):
            raise forms.ValidationError('فقط فایل دیتابیس (.db) مجاز است.')
        return f
