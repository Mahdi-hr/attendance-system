"""فرم‌های ثبت و بررسی مأموریت."""

import datetime as dt

import jdatetime
from django import forms
from django.conf import settings
from django.utils import timezone

from recognition.forms import JalaliDatePickerField, JalaliDateTimePickerField, JalaliDateTimePickerInput

from .models import Errand, ErrandDestination, ErrandSettings


class ErrandForm(forms.ModelForm):
    """فرم ثبت مأموریت توسط کارمند.

    استفاده از JalaliDateTimePickerField برای انتخاب تاریخ/ساعت شمسی.
    """

    # بازهٔ زمانی — JalaliDateTimePickerField با پشتیبانی از پیش‌تنظیم‌ها
    start = JalaliDateTimePickerField(
        label='شروع مأموریت',
        widget=JalaliDateTimePickerInput(attrs={
            'data-field': 'start',
        }),
    )
    end = JalaliDateTimePickerField(
        label='پایان مأموریت',
        widget=JalaliDateTimePickerInput(attrs={
            'data-field': 'end',
        }),
    )
    work_mode = forms.ChoiceField(
        label='نوع مأموریت',
        choices=[],
        widget=forms.RadioSelect(attrs={'class': 'errand-mode-radio'}),
        required=True,
    )
    destination = forms.ChoiceField(
        label='مقصد',
        choices=[],
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
    )

    class Meta:
        model = Errand
        fields = ['start', 'end', 'work_mode', 'reason', 'destination', 'contact']
        widgets = {
            'reason': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 3,
                'placeholder': 'مثلاً: مراجعه به بانک برای کارتابل تسهیلات',
            }),
            'contact': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'اختیاری — شماره‌ای که در طول مأموریت در دسترس است',
            }),
        }

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        self.fields['start'].required = True
        self.fields['end'].required = True
        self.fields['work_mode'].choices = list(Errand.MODE_CHOICES)
        if self.instance and self.instance.pk:
            self.fields['work_mode'].initial = self.instance.work_mode
        else:
            # برای مأموریت جدید، کاربر باید صریحاً ریموت یا شرکتی را انتخاب کند.
            self.fields['work_mode'].initial = ''

        # مقصد فقط از لیست تعریف‌شده توسط ادمین انتخاب می‌شود.
        names = list(
            ErrandDestination.objects.filter(is_active=True)
            .order_by('sort_order', 'name')
            .values_list('name', flat=True)
        )
        choices = [('', '— انتخاب مقصد —')]
        choices.extend((name, name) for name in names)
        current = ''
        if self.instance and self.instance.pk:
            current = (self.instance.destination or '').strip()
        if current and current not in names:
            # مقصد قدیمی که ادمین بعداً غیرفعال/حذف کرده است.
            choices.append((current, current))
        self.fields['destination'].choices = choices

    def clean_work_mode(self):
        value = self.cleaned_data.get('work_mode')
        if value not in (Errand.MODE_REMOTE, Errand.MODE_COMPANY):
            raise forms.ValidationError(
                'نوع مأموریت (ریموت یا شرکتی) را انتخاب کنید.'
            )
        return value

    def _backdate_limit(self):
        """محدودیت روزهای گذشته — قابل تنظیم توسط ادمین از پنل جنگو."""
        return ErrandSettings.load().backdate_limit_days

    def _earliest_allowed(self):
        today = timezone.localdate()
        return today - dt.timedelta(days=self._backdate_limit())

    def save(self, commit=True):
        """نگاشت «start/end» (فیلدهای فرم) به start_time/end_time مدل.

        چون start/end فیلد مدل نیستند، ModelForm به‌تنهایی آن‌ها را روی
        مدل ست نمی‌کند؛ بدون این متد، ذخیره‌سازی با NOT NULL شکست می‌خورد.
        """
        instance = super().save(commit=False)
        cleaned = self.cleaned_data
        if cleaned.get('start') is not None:
            instance.start_time = cleaned['start']
        if cleaned.get('end') is not None:
            instance.end_time = cleaned['end']
        if commit:
            instance.save()
            self.save_m2m()
        return instance

    # ---------- اعتبارسنجی ----------

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get('start')
        end = cleaned.get('end')

        if not start or not end:
            return cleaned

        # ۱) پایان باید بعد از شروع باشد
        if end <= start:
            self.add_error('end', 'پایان مأموریت باید بعد از شروع آن باشد.')
            return cleaned

        # ۲) سقف روزهای گذشته (قابل تنظیم در پنل ادمین)
        earliest = self._earliest_allowed()
        local_start = timezone.localtime(start).date()
        if local_start < earliest:
            limit_days = max((timezone.localdate() - earliest).days, 0)
            self.add_error(
                'start',
                'ثبت مأموریت برای تاریخ‌های قدیمی‌تر از %s روز گذشته مجاز نیست.'
                % limit_days,
            )

        # ۳) سقف ۳۰ روز آینده (جلوگیری از اشتباه تایپی مثل سال)
        latest = timezone.localdate() + dt.timedelta(days=30)
        if timezone.localtime(end).date() > latest:
            self.add_error('end', 'تاریخ پایان بیش از ۳۰ روز در آینده است. احتمالاً اشتباه است.')

        # ۴) همپوشانی با یک درخواست/مأموریت فعالِ دیگرِ خودِ این کاربر
        #    (مأموریت ردشده یا لغوشده مانع ثبت تکراری نیست)
        if 'end' not in self.errors and 'start' not in self.errors and self.user:
            clash = (
                Errand.objects
                .active_overlap(start, end, exclude_pk=self.instance.pk)
                .filter(user=self.user)
                .order_by('start_time')
                .first()
            )
            if clash:
                from recognition.reports import fa_digits
                clash_start = timezone.localtime(clash.start_time)
                clash_end = timezone.localtime(clash.end_time)
                self.add_error(
                    'start',
                    'این بازه با یک مأموریت فعال دیگر همپوشانی دارد '
                    '(%s تا %s). ابتدا آن را لغو یا اصلاح کنید.'
                    % (
                        fa_digits(clash_start.strftime('%H:%M')),
                        fa_digits(clash_end.strftime('%H:%M')),
                    ),
                )

        return cleaned


class ErrandReviewForm(forms.Form):
    """فرم تأیید/رد توسط ادمین."""

    decision = forms.ChoiceField(
        choices=[('approve', 'تأیید'), ('reject', 'رد')],
        widget=forms.HiddenInput(),
    )
    note = forms.CharField(
        required=False,
        max_length=500,
        label='یادداشت',
        widget=forms.Textarea(attrs={
            'class': 'form-control',
            'rows': 2,
            'placeholder': 'اختیاری — هنگام رد کردن، دلیل را بنویسید تا کارمند ببیند.',
        }),
    )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('decision') == 'reject' and not (cleaned.get('note') or '').strip():
            # رد کردن بدون توضیح برای کارمند بی‌فایده است
            self.add_error('note', 'برای رد کردن، نوشتن دلیل الزامی است.')
        return cleaned


class ErrandAdminEditForm(ErrandForm):
    """ویرایش مأموریت توسط ادمین.

    مثل فرم کارمند است با دو تفاوت که عمدی‌اند:

      ۱) محدودیت روزهای گذشته (که در تنظیمات مأموریت قابل تغییر است) اعمال
         نمی‌شود — ادمین باید بتواند رکورد قدیمی را هم اصلاح کند.
      ۲) همپوشانی با درخواست‌های فعال دیگر مجاز است، چون ادمین دارد همان
         رکورد موجود را اصلاح می‌کند و مالکش تغییری نمی‌کند.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # مقدار اولیه: متن شمسی («۱۴۰۵/۰۶/۲۸ ۰۹:۰۰») که هم خودِ فیلد
        # می‌پذیرد و هم datepicker می‌تواند نمایش دهد/انتخاب کند.
        instance = kwargs.get('instance') or self.instance
        if instance and instance.pk:
            for name, attr in (('start', 'start_time'), ('end', 'end_time')):
                value = getattr(instance, attr, None)
                if value:
                    local = timezone.localtime(value) if timezone.is_aware(value) else value
                    j = jdatetime.datetime.fromgregorian(datetime=local)
                    self.initial.setdefault(name, j.strftime('%Y/%m/%d %H:%M'))

    def _earliest_allowed(self):
        # ادمین اجازهٔ اصلاح رکوردهای قدیمی‌تر را دارد
        return dt.date(1970, 1, 1)


class ErrandFilterForm(forms.Form):
    """فیلترهای صفحهٔ بررسی ادمین."""

    date_from = JalaliDatePickerField(label='از تاریخ', required=False)
    date_to = JalaliDatePickerField(label='تا تاریخ', required=False)
    status = forms.ChoiceField(
        required=False,
        choices=[('', 'همه وضعیت‌ها')] + list(Errand.STATUS_CHOICES),
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    q = forms.CharField(
        required=False,
        max_length=60,
        label='جستجو',
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'نام یا نام کاربری کارمند…',
        }),
    )

    def clean(self):
        cleaned = super().clean()
        date_from = cleaned.get('date_from')
        date_to = cleaned.get('date_to')
        if date_from and date_to and date_to < date_from:
            raise forms.ValidationError(
                'بازهٔ تاریخ نامعتبر است: تاریخ پایان باید پس از تاریخ شروع باشد.'
            )
        return cleaned