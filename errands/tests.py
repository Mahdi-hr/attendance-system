"""تست‌های اپ معموریت.

این تست‌ها عمداً روی «حالت‌های مرزی» تمرکز دارند، چون جایی هستند که
یک باگ بی‌صدا باعث می‌شود ساعت کار اشتباه حساب شود.
"""

import datetime as dt

import jdatetime
from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from errands.forms import ErrandForm
from errands.models import Errand
from errands.services import (
    effective_seconds,
    merge_intervals,
    split_interval_by_day,
    subtract_intervals,
    total_seconds,
)
from users.models import Time
from recognition.reports import daily_rows, format_hours, range_summary


def aware(y, m, d, hh=0, mm=0):
    return timezone.make_aware(dt.datetime(y, m, d, hh, mm),
                                timezone.get_current_timezone())


def jal_text(date, hh, mm):
    """تاریخ میلادی → متن شمسی برای ورودی فرم («۱۴۰۵/۰۷/۱۵ ۰۸:۰۰»)."""
    j = jdatetime.datetime.fromgregorian(
        datetime=dt.datetime.combine(date, dt.time(hh, mm)))
    return '%04d/%02d/%02d %02d:%02d' % (j.year, j.month, j.day, hh, mm)


class IntervalMathTests(TestCase):
    """ریاضیات بازه‌ها — بدون نیاز به دیتابیس."""

    def test_merge_adjacent_and_overlapping(self):
        a = (aware(2026, 9, 20, 8), aware(2026, 9, 20, 12))
        b = (aware(2026, 9, 20, 11), aware(2026, 9, 20, 14))
        c = (aware(2026, 9, 20, 14), aware(2026, 9, 20, 16))
        merged = merge_intervals([a, b, c])
        self.assertEqual(len(merged), 1)
        self.assertEqual(total_seconds(merged), 8 * 3600)

    def test_merge_keeps_separate_intervals(self):
        a = (aware(2026, 9, 20, 8), aware(2026, 9, 20, 10))
        b = (aware(2026, 9, 20, 12), aware(2026, 9, 20, 14))
        self.assertEqual(len(merge_intervals([a, b])), 2)

    def test_subtract_removes_overlap(self):
        """معموریت ۸ تا ۱۶ منهای حضور ۸ تا ۱۲ = فقط ۱۲ تا ۱۶."""
        base = [(aware(2026, 9, 20, 8), aware(2026, 9, 20, 16))]
        cuts = [(aware(2026, 9, 20, 8), aware(2026, 9, 20, 12))]
        self.assertEqual(total_seconds(subtract_intervals(base, cuts)), 4 * 3600)

    def test_subtract_cuts_from_middle(self):
        """حضور ۱۰ تا ۱۴ از وسط معموریت ۸ تا ۱۸ → دو تکه: ۲ ساعت + ۴ ساعت."""
        base = [(aware(2026, 9, 20, 8), aware(2026, 9, 20, 18))]   # 10h
        cuts = [(aware(2026, 9, 20, 10), aware(2026, 9, 20, 14))]  # 4h
        result = subtract_intervals(base, cuts)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0], (aware(2026, 9, 20, 8), aware(2026, 9, 20, 10)))
        self.assertEqual(result[1], (aware(2026, 9, 20, 14), aware(2026, 9, 20, 18)))
        self.assertEqual(total_seconds(result), 6 * 3600)

    def test_subtract_full_cover_leaves_nothing(self):
        """اگر کل معموریت با حضور پوشانده شود، معموریت مؤثر صفر است."""
        base = [(aware(2026, 9, 20, 8), aware(2026, 9, 20, 12))]
        cuts = [(aware(2026, 9, 20, 7), aware(2026, 9, 20, 13))]
        self.assertEqual(subtract_intervals(base, cuts), [])

    def test_split_across_midnight(self):
        """۱۷ دیروز تا ۰۴ امروز → دو تکه، مجموع ۱۱ ساعت."""
        segments = split_interval_by_day(
            aware(2026, 9, 20, 17), aware(2026, 9, 21, 4)
        )
        self.assertEqual(len(segments), 2)
        self.assertEqual(segments[0][0], dt.date(2026, 9, 20))
        self.assertEqual(segments[1][0], dt.date(2026, 9, 21))
        self.assertEqual(total_seconds([(s[1], s[2]) for s in segments]), 11 * 3600)

    def test_split_three_days(self):
        segments = split_interval_by_day(
            aware(2026, 9, 20, 20), aware(2026, 9, 22, 8)
        )
        self.assertEqual(len(segments), 3)
        self.assertEqual(total_seconds([(s[1], s[2]) for s in segments]), 36 * 3600)

    def test_split_rejects_inverted_range(self):
        self.assertEqual(
            split_interval_by_day(aware(2026, 9, 20, 12), aware(2026, 9, 20, 8)), []
        )

    def test_effective_seconds_prefers_union_not_double_count(self):
        """مهم‌ترین تست: ۴ ساعت حضور + ۸ ساعت معموریتِ همپوشان = ۸ ساعت، نه ۱۲."""
        work = [(aware(2026, 9, 20, 8), aware(2026, 9, 20, 12))]
        errand = [(aware(2026, 9, 20, 8), aware(2026, 9, 20, 16))]
        effective = effective_seconds(errand, work)
        self.assertEqual(effective, 4 * 3600)
        # و مجموع نهایی = ۸ ساعت
        total = 4 * 3600 + effective
        self.assertEqual(total, 8 * 3600)


class ErrandModelTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user('ali', password='x')
        self.admin = User.objects.create_superuser('boss', 'b@b.com', 'x')

    def make(self, start, end, **kw):
        return Errand.objects.create(
            user=self.user, start_time=start, end_time=end,
            reason='مراجعه به بانک', **kw
        )

    def test_approve_sets_reviewer_and_time(self):
        e = self.make(aware(2026, 9, 20, 17), aware(2026, 9, 21, 4))
        self.assertTrue(e.approve(self.admin, 'تأیید'))
        e.refresh_from_db()
        self.assertEqual(e.status, Errand.STATUS_APPROVED)
        self.assertEqual(e.reviewed_by_id, self.admin.pk)
        self.assertIsNotNone(e.reviewed_at)

    def test_reject_with_note(self):
        e = self.make(aware(2026, 9, 20, 17), aware(2026, 9, 21, 4))
        self.assertTrue(e.reject(self.admin, 'بدون هماهنگی'))
        e.refresh_from_db()
        self.assertEqual(e.status, Errand.STATUS_REJECTED)
        self.assertEqual(e.review_note, 'بدون هماهنگی')

    def test_cannot_approve_twice(self):
        e = self.make(aware(2026, 9, 20, 17), aware(2026, 9, 21, 4))
        self.assertTrue(e.approve(self.admin))
        self.assertFalse(e.approve(self.admin))  # بار دوم نباید اثر کند

    def test_cancel_only_pending_and_own(self):
        e = self.make(aware(2026, 9, 20, 17), aware(2026, 9, 21, 4))
        other = User.objects.create_user('sara', password='x')
        self.assertFalse(e.cancel(other))       # کاربر دیگر نمی‌تواند
        self.assertTrue(e.cancel(self.user))
        e.refresh_from_db()
        self.assertEqual(e.status, Errand.STATUS_CANCELLED)
        # بعد از لغو، دیگر تأیید نمی‌شود
        self.assertFalse(e.approve(self.admin))

    def test_duration_over_night(self):
        e = self.make(aware(2026, 9, 20, 17), aware(2026, 9, 21, 4))
        self.assertEqual(e.duration_seconds, 11 * 3600)
        self.assertEqual(e.duration_compact(), '11:00')

    def test_check_constraint_blocks_inverted_range(self):
        from django.db import IntegrityError, transaction
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.make(aware(2026, 9, 20, 12), aware(2026, 9, 20, 8))


class ReportIntegrationTests(TestCase):
    """معموریت باید در گزارش‌ها درست بنشیند."""

    def setUp(self):
        self.user = User.objects.create_user('ali', password='x')
        self.date = dt.date(2026, 9, 20)

    def add_time(self, when, out):
        Time.objects.create(user=self.user, date=self.date, time=when, out=out)

    def test_approved_errand_only_counts_after_approval(self):
        """قبل از تأیید نباید هیچ اثری روی ساعت کار باشد."""
        e = Errand.objects.create(
            user=self.user,
            start_time=aware(2026, 9, 20, 8),
            end_time=aware(2026, 9, 20, 16),
            reason='مأموریت',
            status=Errand.STATUS_PENDING,
        )
        rows = daily_rows([self.user], self.date, self.date)
        self.assertEqual(len(rows), 0)  # اصلاً در گزارش نیست

        e.approve(User.objects.create_superuser('boss', 'b@b.com', 'x'))
        rows = daily_rows([self.user], self.date, self.date)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['errand_seconds'], 8 * 3600)
        self.assertEqual(rows[0]['total_seconds'], 8 * 3600)
        self.assertTrue(rows[0]['present'])

    def test_errand_day_without_any_attendance_still_shows(self):
        """کارمندی که کل روز مأموریت بوده و اصلاً وارد نشده."""
        e = Errand.objects.create(
            user=self.user,
            start_time=aware(2026, 9, 20, 9),
            end_time=aware(2026, 9, 20, 17),
            reason='مأموریت',
            status=Errand.STATUS_APPROVED,
        )
        rows = daily_rows([self.user], self.date, self.date)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['work_seconds'], 0)
        self.assertEqual(rows[0]['errand_seconds'], 8 * 3600)
        self.assertEqual(rows[0]['total_seconds'], 8 * 3600)
        self.assertIsNone(rows[0]['first_in'])

    def test_no_double_count_with_physical_attendance(self):
        """حضور ۸ تا ۱۲ + معموریت ۱۰ تا ۱۸ → ۴ + ۸ = ۱۲ ساعت (نه ۱۴)."""
        self.add_time(aware(2026, 9, 20, 8), out=False)
        self.add_time(aware(2026, 9, 20, 12), out=True)
        Errand.objects.create(
            user=self.user,
            start_time=aware(2026, 9, 20, 10),
            end_time=aware(2026, 9, 20, 18),
            reason='مأموریت',
            status=Errand.STATUS_APPROVED,
        )
        rows = daily_rows([self.user], self.date, self.date)
        self.assertEqual(rows[0]['work_seconds'], 4 * 3600)
        self.assertEqual(rows[0]['errand_seconds'], 6 * 3600)   # ۱۲ تا ۱۸
        self.assertEqual(rows[0]['total_seconds'], 10 * 3600)   # ۸ تا ۱۸

    def test_errand_spanning_midnight_splits_across_days(self):
        """معموریت ۲۰ دیروز تا ۰۴ امروز → ۴ ساعت دیروز + ۴ ساعت امروز."""
        Errand.objects.create(
            user=self.user,
            start_time=aware(2026, 9, 19, 20),
            end_time=aware(2026, 9, 20, 4),
            reason='شبانه',
            status=Errand.STATUS_APPROVED,
        )
        rows = daily_rows([self.user], dt.date(2026, 9, 19), dt.date(2026, 9, 20))
        self.assertEqual(len(rows), 2)
        by_date = {r['date']: r for r in rows}
        self.assertEqual(by_date[dt.date(2026, 9, 19)]['errand_seconds'], 4 * 3600)
        self.assertEqual(by_date[dt.date(2026, 9, 20)]['errand_seconds'], 4 * 3600)
        days, total, _ = range_summary(rows)
        self.assertEqual(total, 8 * 3600)
        self.assertEqual(days, 2)

    def test_rejected_errand_never_counts(self):
        Errand.objects.create(
            user=self.user,
            start_time=aware(2026, 9, 20, 9),
            end_time=aware(2026, 9, 20, 17),
            reason='مأموریت',
            status=Errand.STATUS_REJECTED,
        )
        rows = daily_rows([self.user], self.date, self.date)
        self.assertEqual(len(rows), 0)

    def test_two_overlapping_errands_not_double_counted(self):
        Errand.objects.create(
            user=self.user, start_time=aware(2026, 9, 20, 9),
            end_time=aware(2026, 9, 20, 15), reason='الف',
            status=Errand.STATUS_APPROVED)
        Errand.objects.create(
            user=self.user, start_time=aware(2026, 9, 20, 12),
            end_time=aware(2026, 9, 20, 18), reason='ب',
            status=Errand.STATUS_APPROVED)
        rows = daily_rows([self.user], self.date, self.date)
        # ۹ تا ۱۸ = ۹ ساعت، نه ۶ + ۶ = ۱۲
        self.assertEqual(rows[0]['errand_seconds'], 9 * 3600)


# ============================================================
#  فیلد انتخاب تاریخ/ساعت شمسی
# ============================================================

class DateTimePickerFieldTests(SimpleTestCase):
    """ JalaliDateTimePickerField باید متن شمسی را به datetime آگاه تبدیل کند. """

    def clean(self, value):
        from recognition.forms import JalaliDateTimePickerField
        return JalaliDateTimePickerField().clean(value)

    def test_latin_digits_parsed(self):
        result = self.clean('1405/07/15 17:30')
        self.assertTrue(timezone.is_aware(result))
        self.assertEqual(result.hour, 17)
        self.assertEqual(result.minute, 30)
        # ۱۵ مهر ۱۴۰۵ شمسی = ۷ اکتبر ۲۰۲۶ میلادی
        self.assertEqual(result.date(), dt.date(2026, 10, 7))

    def test_persian_digits_parsed(self):
        result = self.clean('۱۴۰۵/۰۷/۱۵ ۰۸:۰۵')
        self.assertTrue(timezone.is_aware(result))
        self.assertEqual(result.hour, 8)
        self.assertEqual(result.minute, 5)

    def test_garbage_value_gives_friendly_error(self):
        """«پیغام خطا الکی» نباید بزند — پیام باید راهنمایی‌کننده باشد."""
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError) as ctx:
            self.clean('فردا صبح')
        self.assertIn('قالب', str(ctx.exception.messages[0]))

    def test_invalid_hour_rejected(self):
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            self.clean('1405/07/15 27:99')

    def test_empty_returns_none_when_not_required(self):
        from recognition.forms import JalaliDateTimePickerField
        field = JalaliDateTimePickerField(required=False)
        self.assertIsNone(field.clean(''))


# ============================================================
#  فرم ثبت معموریت — اعتبارسنجی
# ============================================================

class ErrandFormTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user('ali', password='x')
        self.today = timezone.localdate()

    def make_form(self, start, end, **extra):
        data = {'start': start, 'end': end,
                'reason': extra.pop('reason', 'مراجعه به بانک')}
        data.update(extra)
        return ErrandForm(data=data, user=self.user)

    def test_valid_form_parses_to_aware_datetimes(self):
        form = self.make_form(
            jal_text(self.today, 8, 0), jal_text(self.today, 16, 0))
        self.assertTrue(form.is_valid(), form.errors)
        start = form.cleaned_data['start']
        self.assertTrue(timezone.is_aware(start))
        self.assertEqual(timezone.localtime(start).hour, 8)
        self.assertEqual(
            timezone.localtime(form.cleaned_data['end']).hour, 16)

    def test_overnight_range_is_valid(self):
        """۱۷ دیروز تا ۰۴ امروز باید مجاز باشد (کار اصلی کاربر)."""
        form = self.make_form(
            jal_text(self.today - dt.timedelta(days=1), 17, 0),
            jal_text(self.today, 4, 0),
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_inverted_range_rejected_with_clear_message(self):
        form = self.make_form(
            jal_text(self.today, 16, 0), jal_text(self.today, 8, 0))
        self.assertFalse(form.is_valid())
        self.assertIn('end', form.errors)
        self.assertIn('بعد از شروع', form.errors['end'][0])

    def test_malformed_datetime_rejected_not_crashing(self):
        """رورودی بی‌معنی نباید ۵۰۰ بدهد؛ فقط خطای اعتبارسنجی."""
        form = self.make_form('یه وقتی فردا', '1405/07/15 16:00')
        self.assertFalse(form.is_valid())
        self.assertIn('start', form.errors)

    def test_backdate_beyond_seven_days_rejected(self):
        old = self.today - dt.timedelta(days=10)
        form = self.make_form(jal_text(old, 8, 0), jal_text(old, 16, 0))
        self.assertFalse(form.is_valid())
        self.assertIn('start', form.errors)

    def test_backdate_within_seven_days_allowed(self):
        recent = self.today - dt.timedelta(days=6)
        form = self.make_form(
            jal_text(recent, 8, 0), jal_text(recent, 16, 0))
        self.assertTrue(form.is_valid(), form.errors)

    def test_far_future_end_rejected(self):
        far = self.today + dt.timedelta(days=45)
        form = self.make_form(
            jal_text(self.today, 8, 0), jal_text(far, 16, 0))
        self.assertFalse(form.is_valid())
        self.assertIn('end', form.errors)

    def test_overlap_with_pending_request_rejected(self):
        Errand.objects.create(
            user=self.user,
            start_time=aware(*self.today.timetuple()[:3], 9),
            end_time=aware(*self.today.timetuple()[:3], 11),
            reason='قبلاً ثبت شده',
            status=Errand.STATUS_PENDING,
        )
        form = self.make_form(
            jal_text(self.today, 10, 0),
            jal_text(self.today, 12, 0),
        )
        self.assertFalse(form.is_valid())
        self.assertIn('همپوشانی', form.errors.get('start', [''])[0])

    def test_rejected_request_does_not_block_new_one(self):
        Errand.objects.create(
            user=self.user,
            start_time=aware(*self.today.timetuple()[:3], 9),
            end_time=aware(*self.today.timetuple()[:3], 11),
            reason='قبلاً رد شده',
            status=Errand.STATUS_REJECTED,
        )
        form = self.make_form(
            jal_text(self.today, 9, 0),
            jal_text(self.today, 11, 0),
        )
        self.assertTrue(form.is_valid(), form.errors)


# ============================================================
#  ویوهای کارمند — صفحهٔ /errands/
# ============================================================

class EmployeeErrandViewTests(TestCase):

    def setUp(self):
        self.employee = User.objects.create_user('ali', password='x')
        self.admin = User.objects.create_superuser('boss', 'b@b.com', 'x')
        self.today = timezone.localdate()
        self.url = reverse('errands-my')

    def login(self, user):
        self.client.force_login(user)

    def valid_post(self, **over):
        data = {
            'start': jal_text(self.today, 8, 0),
            'end': jal_text(self.today, 16, 0),
            'reason': 'مراجعه به بانک',
            'destination': 'بانک ملت',
            'contact': '',
        }
        data.update(over)
        return data

    def test_login_required(self):
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_page_renders_with_datetime_picker_and_presets(self):
        self.login(self.employee)
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        # ورودی تاریخ/ساعت شمسی
        self.assertContains(r, 'data-time="true"')
        # میانبرهای شبیه بخش گزارشات
        for preset in ('today', 'week', 'currentMonth'):
            self.assertContains(r, 'data-errand-preset="%s"' % preset)
        self.assertContains(r, 'ثبت معموریت جدید')

    def test_employee_creates_pending_errand(self):
        self.login(self.employee)
        r = self.client.post(self.url, self.valid_post())
        self.assertRedirects(r, self.url)
        e = Errand.objects.get()
        self.assertEqual(e.user, self.employee)
        self.assertEqual(e.status, Errand.STATUS_PENDING)
        self.assertEqual(
            timezone.localtime(e.start_time).hour, 8)
        self.assertEqual(
            timezone.localtime(e.end_time).hour, 16)

    def test_invalid_post_renders_errors_without_500(self):
        """ورودی خراب نباید ۵۰۰ بدهد؛ باید همان صفحه با پیام خطا برگردد."""
        self.login(self.employee)
        r = self.client.post(self.url, self.valid_post(
            start='یه وقتی فردا', end='1405/07/15 16:00'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'قالب تاریخ و ساعت نامعتبر است')
        self.assertEqual(Errand.objects.count(), 0)

    def test_inverted_post_shows_field_error(self):
        self.login(self.employee)
        r = self.client.post(self.url, self.valid_post(
            start=jal_text(self.today, 16, 0),
            end=jal_text(self.today, 8, 0)))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'بعد از شروع')
        self.assertEqual(Errand.objects.count(), 0)

    def test_admin_mode_hides_create_form(self):
        """در حالت ادمین نباید فرم ثبت دیده شود — فقط بررسی مجاز است."""
        self.login(self.admin)
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertIs(r.context['form'], None)
        self.assertTrue(r.context['is_admin_mode'])
        self.assertNotContains(r, 'ثبت معموریت جدید')
        # ولی فهرست همچنان دیده می‌شود
        self.assertContains(r, 'معموریت‌های من')

    def test_admin_mode_ignores_create_post(self):
        self.login(self.admin)
        r = self.client.post(self.url, self.valid_post())
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Errand.objects.count(), 0)

    def test_employee_sees_rejected_reason(self):
        Errand.objects.create(
            user=self.employee,
            start_time=aware(2026, 9, 20, 9),
            end_time=aware(2026, 9, 20, 17),
            reason='مراجعه به دادگستری',
            status=Errand.STATUS_REJECTED,
            review_note='بدون هماهنگی قبلی رد شد',
        )
        self.login(self.employee)
        r = self.client.get(self.url)
        self.assertContains(r, 'بدون هماهنگی قبلی رد شد')
        self.assertContains(r, 'دلیل رد توسط مدیر')

    def test_page_renders_with_approved_overnight_errand(self):
        """رجرسیون: آمار معموریت تأییدشده قبلاً ۵۰۰ می‌داد (unpack سه‌تایی)."""
        Errand.objects.create(
            user=self.employee,
            start_time=aware(2026, 9, 20, 17),
            end_time=aware(2026, 9, 21, 4),
            reason='شبانه',
            status=Errand.STATUS_APPROVED,
        )
        self.login(self.employee)
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(
            r.context['approved_text'], format_hours(11 * 3600))

    def test_cancel_pending_own_request(self):
        e = Errand.objects.create(
            user=self.employee,
            start_time=aware(2026, 9, 20, 9),
            end_time=aware(2026, 9, 20, 17),
            reason='مأموریت',
            status=Errand.STATUS_PENDING,
        )
        self.login(self.employee)
        r = self.client.post(reverse('errands-cancel', args=[e.pk]))
        self.assertRedirects(r, self.url)
        e.refresh_from_db()
        self.assertEqual(e.status, Errand.STATUS_CANCELLED)

    def test_cannot_cancel_someone_elses_request(self):
        other = User.objects.create_user('sara', password='x')
        e = Errand.objects.create(
            user=other,
            start_time=aware(2026, 9, 20, 9),
            end_time=aware(2026, 9, 20, 17),
            reason='مأموریت',
            status=Errand.STATUS_PENDING,
        )
        self.login(self.employee)
        self.client.post(reverse('errands-cancel', args=[e.pk]))
        e.refresh_from_db()
        self.assertEqual(e.status, Errand.STATUS_PENDING)


# ============================================================
#  ویوهای ادمین — صف بررسی /manage/errands/
# ============================================================

class AdminReviewViewTests(TestCase):

    def setUp(self):
        self.employee = User.objects.create_user('ali', password='x')
        self.admin = User.objects.create_superuser('boss', 'b@b.com', 'x')
        self.url = reverse('errands-admin')

    def login(self, user):
        self.client.force_login(user)

    def make(self, **kw):
        kw.setdefault('user', self.employee)
        kw.setdefault('start_time', aware(2026, 9, 20, 9))
        kw.setdefault('end_time', aware(2026, 9, 20, 17))
        kw.setdefault('reason', 'مأموریت')
        kw.setdefault('status', Errand.STATUS_PENDING)
        return Errand.objects.create(**kw)

    # ---------- رندر شدن (رجرسیون ۵۰۰) ----------

    def test_review_page_renders_with_pending_errand(self):
        """رجرسیون: این صفحه قبلاً ۵۰۰ می‌داد (total_seconds روی سه‌تایی)."""
        overnight = self.make(
            start_time=aware(2026, 9, 20, 17),
            end_time=aware(2026, 9, 21, 4))
        self.login(self.admin)
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context['pending_count'], 1)
        self.assertEqual(
            r.context['pending_text'], format_hours(11 * 3600))
        self.assertContains(r, overnight.user.username)

    def test_review_page_renders_with_no_pending(self):
        self.login(self.admin)
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context['pending_count'], 0)

    def test_non_admin_redirected(self):
        self.login(self.employee)
        r = self.client.get(self.url)
        self.assertRedirects(r, reverse('not-authorised'))

    # ---------- تأیید / رد ----------

    def test_approve_pending(self):
        e = self.make()
        self.login(self.admin)
        r = self.client.post(
            reverse('errands-review', args=[e.pk]),
            {'decision': 'approve', 'note': ''})
        self.assertRedirects(r, self.url)
        e.refresh_from_db()
        self.assertEqual(e.status, Errand.STATUS_APPROVED)
        self.assertEqual(e.reviewed_by_id, self.admin.pk)

    def test_reject_requires_note(self):
        e = self.make()
        self.login(self.admin)
        r = self.client.post(
            reverse('errands-review', args=[e.pk]),
            {'decision': 'reject', 'note': ''})
        # ریدایرکت با fragment («#errand-N») — فقط سربرگ را چک می‌کنیم
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r.url.startswith(self.url))
        e.refresh_from_db()
        self.assertEqual(e.status, Errand.STATUS_PENDING)

    def test_reject_with_note_saves_reason(self):
        e = self.make()
        self.login(self.admin)
        self.client.post(
            reverse('errands-review', args=[e.pk]),
            {'decision': 'reject', 'note': 'با برنامهٔ هفتگی تداخل دارد'})
        e.refresh_from_db()
        self.assertEqual(e.status, Errand.STATUS_REJECTED)
        self.assertEqual(e.review_note, 'با برنامهٔ هفتگی تداخل دارد')

    def test_invalid_decision_rejected(self):
        e = self.make()
        self.login(self.admin)
        self.client.post(
            reverse('errands-review', args=[e.pk]),
            {'decision': 'maybe', 'note': ''})
        e.refresh_from_db()
        self.assertEqual(e.status, Errand.STATUS_PENDING)

    # ---------- بررسی گروهی ----------

    def test_bulk_approve(self):
        a = self.make()
        b = self.make(start_time=aware(2026, 9, 21, 9),
                      end_time=aware(2026, 9, 21, 17))
        self.login(self.admin)
        self.client.post(reverse('errands-bulk'), {
            'errand_ids': [str(a.pk), str(b.pk)],
            'bulk_decision': 'approve',
        })
        a.refresh_from_db()
        b.refresh_from_db()
        self.assertEqual(a.status, Errand.STATUS_APPROVED)
        self.assertEqual(b.status, Errand.STATUS_APPROVED)

    def test_bulk_reject_requires_note(self):
        e = self.make()
        self.login(self.admin)
        self.client.post(reverse('errands-bulk'), {
            'errand_ids': [str(e.pk)],
            'bulk_decision': 'reject',
        })
        e.refresh_from_db()
        self.assertEqual(e.status, Errand.STATUS_PENDING)

    def test_bulk_with_no_selection_warns(self):
        self.login(self.admin)
        r = self.client.post(reverse('errands-bulk'), {
            'bulk_decision': 'approve'})
        self.assertRedirects(r, self.url)

    # ---------- ویرایش توسط ادمین ----------

    def test_admin_edit_get_renders(self):
        e = self.make()
        self.login(self.admin)
        r = self.client.get(reverse('errands-edit', args=[e.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(
            r.context['start_value'], '2026-09-20T09:00')

    def test_admin_edit_post_updates_range(self):
        e = self.make()
        self.login(self.admin)
        tomorrow = timezone.localdate() + dt.timedelta(days=1)
        r = self.client.post(reverse('errands-edit', args=[e.pk]), {
            'start': jal_text(tomorrow, 10, 0),
            'end': jal_text(tomorrow, 15, 30),
            'reason': 'بازه اصلاح شد',
        })
        self.assertRedirects(r, self.url)
        e.refresh_from_db()
        self.assertEqual(timezone.localtime(e.start_time).hour, 10)
        self.assertEqual(timezone.localtime(e.end_time).minute, 30)
        self.assertEqual(e.reason, 'بازه اصلاح شد')

    def test_admin_edit_rejects_inverted_range(self):
        e = self.make()
        self.login(self.admin)
        r = self.client.post(reverse('errands-edit', args=[e.pk]), {
            'start': jal_text(timezone.localdate(), 16, 0),
            'end': jal_text(timezone.localdate(), 8, 0),
            'reason': 'اشتباه',
        })
        self.assertEqual(r.status_code, 200)
        e.refresh_from_db()
        self.assertEqual(
            timezone.localtime(e.start_time).hour, 9)  # دست‌نخورده


# ============================================================
#  فهرست و خروجی اکسل
# ============================================================

class ErrandListExportTests(TestCase):

    def setUp(self):
        self.employee = User.objects.create_user('ali', password='x')
        self.admin = User.objects.create_superuser('boss', 'b@b.com', 'x')
        self.approved = Errand.objects.create(
            user=self.employee,
            start_time=aware(2026, 9, 20, 9),
            end_time=aware(2026, 9, 20, 17),
            reason='بازدید کارگاه',
            status=Errand.STATUS_APPROVED,
        )
        self.pending = Errand.objects.create(
            user=self.employee,
            start_time=aware(2026, 9, 21, 9),
            end_time=aware(2026, 9, 21, 17),
            reason='در انتظار',
            status=Errand.STATUS_PENDING,
        )
        self.client.force_login(self.admin)

    def test_list_defaults_to_approved_only(self):
        r = self.client.get(reverse('errands-list'))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context['count'], 1)
        self.assertContains(r, 'بازدید کارگاه')

    def test_list_status_filter(self):
        r = self.client.get(reverse('errands-list'),
                            {'status': 'pending'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context['count'], 1)
        self.assertContains(r, 'در انتظار')

    def test_export_returns_xlsx(self):
        r = self.client.get(reverse('errands-export'))
        self.assertEqual(r.status_code, 200)
        self.assertIn('spreadsheet', r['Content-Type'])
        self.assertTrue(r.content.startswith(b'PK'))  # سربرگ ZIP/xlsx

    def test_list_requires_admin(self):
        self.client.force_login(self.employee)
        r = self.client.get(reverse('errands-list'))
        self.assertRedirects(r, reverse('not-authorised'))


class MyErrandsSectionsTests(TestCase):
    """«معموریت‌های من» باید دو بخش جدا داشته باشد و هر بخش حداکثر ۳ کارت
    در هر صفحه نشان دهد (تا صفحه شلوغ نشود)."""

    def setUp(self):
        self.employee = User.objects.create_user('reza', password='x')
        self.admin = User.objects.create_superuser('boss', 'b@b.com', 'x')
        self.today = timezone.localdate()
        self.url = reverse('errands-my')
        self.client.force_login(self.employee)

    def make(self, count, status, base_day=None):
        """ساخت `count` درخواست با روزهای پیاپی (نزدیک‌ترین، اول)."""
        base_day = base_day or self.today
        made = []
        for i in range(count):
            day = base_day - dt.timedelta(days=i)
            errand = Errand.objects.create(
                user=self.employee,
                start_time=aware(day.year, day.month, day.day, 8, 0),
                end_time=aware(day.year, day.month, day.day, 12, 0),
                reason='مأموریت شماره %d' % i,
            )
            if status == Errand.STATUS_APPROVED:
                errand.approve(self.admin)
            elif status == Errand.STATUS_REJECTED:
                errand.reject(self.admin, note='با برنامهٔ شیفت همپوشانی دارد')
            made.append(errand)
        return made

    def test_context_has_two_paginated_sections(self):
        self.make(2, Errand.STATUS_PENDING)
        self.make(1, Errand.STATUS_APPROVED,
                  self.today - dt.timedelta(days=10))
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context['pending_page'].paginator.count, 2)
        self.assertEqual(r.context['done_page'].paginator.count, 1)
        self.assertEqual(r.context['mine_count'], 3)

    def test_pending_section_shows_max_three_cards(self):
        self.make(5, Errand.STATUS_PENDING)
        r = self.client.get(self.url)
        self.assertEqual(len(r.context['pending_page']), 3)
        self.assertEqual(r.context['pending_page'].paginator.count, 5)

    def test_pending_second_page_has_the_rest(self):
        self.make(5, Errand.STATUS_PENDING)
        r = self.client.get(self.url + '?pending_page=2')
        self.assertEqual(r.context['pending_page'].number, 2)
        self.assertEqual(len(r.context['pending_page']), 2)

    def test_done_section_paginates_independently(self):
        self.make(4, Errand.STATUS_REJECTED,
                  self.today - dt.timedelta(days=20))
        r = self.client.get(self.url)
        self.assertEqual(len(r.context['done_page']), 3)
        r2 = self.client.get(self.url + '?done_page=2')
        self.assertEqual(r2.context['done_page'].number, 2)
        self.assertEqual(len(r2.context['done_page']), 1)

    def test_sections_have_headings_and_nav(self):
        self.make(4, Errand.STATUS_PENDING)
        r = self.client.get(self.url)
        self.assertContains(r, 'در انتظار بررسی')
        self.assertContains(r, 'درخواست‌های بررسی‌شده')
        # لینک صفحهٔ بعدِ همان بخش
        self.assertContains(r, 'pending_page=2')

    def test_empty_sections_show_friendly_notes(self):
        r = self.client.get(self.url)
        self.assertContains(r, 'هنوز معموریتی ثبت نکرده‌اید')

    def test_both_sections_mixed(self):
        """دو بخش هم‌زمان: ۳ در انتظار + ۳ بررسی‌شده در هر کدام حداکثر ۳."""
        self.make(4, Errand.STATUS_PENDING)
        self.make(3, Errand.STATUS_APPROVED,
                  self.today - dt.timedelta(days=20))
        r = self.client.get(self.url)
        self.assertEqual(len(r.context['pending_page']), 3)
        self.assertEqual(len(r.context['done_page']), 3)
        self.assertEqual(r.context['pending_page'].paginator.count, 4)
        self.assertEqual(r.context['done_page'].paginator.count, 3)


class ErrandAdminTests(TestCase):
    """پنل ادمین جنگو برای معموریت‌ها — /admin/errands/errand/."""

    def setUp(self):
        self.boss = User.objects.create_superuser('boss', 'b@b.com', 'x')
        self.ali = User.objects.create_user(
            'ali', password='x', first_name='علی')
        self.url = reverse('admin:errands_errand_changelist')
        self.today = timezone.localdate()
        self.client.force_login(self.boss)

    def make(self, user=None, day=None, status=None):
        day = day or self.today
        errand = Errand.objects.create(
            user=user or self.ali,
            start_time=aware(day.year, day.month, day.day, 8, 0),
            end_time=aware(day.year, day.month, day.day, 12, 0),
            reason='مراجعه به بانک',
            destination='شعبهٔ مرکزی',
        )
        if status == Errand.STATUS_APPROVED:
            errand.approve(self.boss)
        elif status == Errand.STATUS_REJECTED:
            errand.reject(self.boss, note='دلیل قدیمی')
        return errand

    def act(self, action, errands):
        """POST کردن عملیات گروهی (دیجنگو بعد از عملیات ۳۰۲ می‌زند)."""
        r = self.client.post(self.url, {
            'action': action,
            '_selected_action': [str(e.pk) for e in errands],
            'select_across': '0',
            'index': '0',
        })
        self.assertIn(r.status_code, (200, 302))
        return r

    # ---------- فهرست و فیلترها ----------

    def test_changelist_renders_with_jalali_columns(self):
        self.make()
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'بازه (تهران)')
        self.assertContains(r, 'مدت')
        self.assertContains(r, 'در انتظار بررسی')   # نشان وضعیت
        self.assertContains(r, 'بررسی در پنل')       # لینک عملیات سریع

    def test_search_by_username_and_reason(self):
        self.make()
        self.make(user=User.objects.create_user('reza', first_name='رضا'),
                  day=self.today - dt.timedelta(days=1))
        r = self.client.get(self.url, {'q': 'ali'})
        self.assertContains(r, 'علی (@ali)')
        self.assertNotContains(r, 'رضا (@reza)')
        r2 = self.client.get(self.url, {'q': 'بانک'})
        self.assertContains(r2, 'علی (@ali)')

    def test_status_filter(self):
        self.make(status=Errand.STATUS_APPROVED)
        r = self.client.get(self.url, {'status': 'pending'})
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, 'بررسی در پنل')  # ردیف تأییدشده نیست

    # ---------- عملیات گروهی ----------

    def test_approve_action_sets_reviewer(self):
        errand = self.make()
        self.act('approve_selected', [errand])
        errand.refresh_from_db()
        self.assertEqual(errand.status, Errand.STATUS_APPROVED)
        self.assertEqual(errand.reviewed_by, self.boss)
        self.assertIsNotNone(errand.reviewed_at)

    def test_approve_action_skips_already_reviewed(self):
        errand = self.make(status=Errand.STATUS_APPROVED)
        self.make(day=self.today - dt.timedelta(days=1))
        self.act('approve_selected', [errand])
        errand.refresh_from_db()
        self.assertEqual(errand.reviewed_by, self.boss)

    def test_reject_action_redirects_to_bulk_page(self):
        errand = self.make()
        r = self.act('reject_selected', [errand])
        expected = reverse('admin:errands_errand_bulk_reject')
        self.assertRedirects(r, '%s?ids=%d' % (expected, errand.pk))

    def test_reset_action_returns_to_pending(self):
        errand = self.make(status=Errand.STATUS_APPROVED)
        self.act('reset_selected', [errand])
        errand.refresh_from_db()
        self.assertEqual(errand.status, Errand.STATUS_PENDING)
        self.assertIsNone(errand.reviewed_by)
        self.assertEqual(errand.review_note, '')

    def test_cancel_action(self):
        errand = self.make()
        self.act('cancel_selected', [errand])
        errand.refresh_from_db()
        self.assertEqual(errand.status, Errand.STATUS_CANCELLED)

    def test_export_action_returns_xlsx(self):
        self.make()
        r = self.act('export_selected', list(Errand.objects.all()))
        self.assertEqual(r.status_code, 200)
        self.assertIn('spreadsheet', r['Content-Type'])
        self.assertTrue(r.content.startswith(b'PK'))

    # ---------- صفحهٔ میانی «رد گروهی» ----------

    def test_bulk_reject_page_lists_selection(self):
        errand = self.make()
        url = reverse('admin:errands_errand_bulk_reject')
        r = self.client.get(url, {'ids': str(errand.pk)})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'دلیل رد')
        self.assertContains(r, 'علی (@ali)')
        self.assertContains(r, 'مراجعه به بانک')

    def test_bulk_reject_without_note_keeps_pending(self):
        errand = self.make()
        url = reverse('admin:errands_errand_bulk_reject')
        r = self.client.post(url, {'ids': str(errand.pk), 'note': '   '})
        self.assertEqual(r.status_code, 200)  # همان صفحه با خطا
        errand.refresh_from_db()
        self.assertEqual(errand.status, Errand.STATUS_PENDING)

    def test_bulk_reject_with_note_rejects_and_records(self):
        errand = self.make()
        url = reverse('admin:errands_errand_bulk_reject')
        r = self.client.post(url, {
            'ids': str(errand.pk),
            'note': 'با برنامهٔ شیفت همپوشانی دارد',
        })
        self.assertRedirects(r, self.url)
        errand.refresh_from_db()
        self.assertEqual(errand.status, Errand.STATUS_REJECTED)
        self.assertEqual(errand.review_note, 'با برنامهٔ شیفت همپوشانی دارد')
        self.assertEqual(errand.reviewed_by, self.boss)

    def test_bulk_reject_skips_already_reviewed(self):
        errand = self.make(status=Errand.STATUS_APPROVED)
        url = reverse('admin:errands_errand_bulk_reject')
        self.client.post(url, {'ids': str(errand.pk), 'note': 'دلیل تازه'})
        errand.refresh_from_db()
        self.assertEqual(errand.status, Errand.STATUS_APPROVED)

    def test_bulk_reject_get_without_ids_is_safe(self):
        url = reverse('admin:errands_errand_bulk_reject')
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'هیچ درخواستی برای رد پیدا نشد')

    # ---------- فرم تک‌موردی ----------

    def test_save_model_auto_sets_reviewer(self):
        from django.contrib import admin as django_admin
        from django.test import RequestFactory

        from errands.admin import ErrandAdmin

        errand = self.make()
        errand.status = Errand.STATUS_APPROVED
        request = RequestFactory().post(
            '/admin/errands/errand/%d/change/' % errand.pk)
        request.user = self.boss
        ErrandAdmin(Errand, django_admin.site).save_model(
            request, errand, form=None, change=True)
        errand.refresh_from_db()
        self.assertEqual(errand.status, Errand.STATUS_APPROVED)
        self.assertEqual(errand.reviewed_by, self.boss)
        self.assertIsNotNone(errand.reviewed_at)

    def test_non_staff_cannot_open_admin(self):
        self.client.force_login(
            User.objects.create_user('noship', password='x'))
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 302)
        self.assertIn('/admin/login/', r['Location'])