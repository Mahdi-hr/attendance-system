"""تست‌های فیلتر، صفحه‌بندی و «بازگشایی» صفحهٔ خطاهای خروج.

صفحهٔ /checkout-errors/ بعد از اضافه‌شدن فیلتر (جستجو/وضعیت/بازهٔ تاریخ
شمسی + میانبرها)، صفحه‌بندی جدول «رفع‌شده»ها و دکمهٔ «بازگشایی» باید:

- فقط برای ادمین قابل دسترسی باشد؛
- با ورودی‌های خراب (تاریخ/وضعیت دست‌ساز) نه ۵۰۰ بدهد و نه بی‌صدا فیلتر
  عجیب اعمال کند؛
- «بازگشایی» فقط خطای «رفع‌شده» را به «در انتظار» برگرداند و زمان خروج
  ثبت‌شده را به حالت خودکار (۲۳:۵۹:۵۹) بازگرداند.
"""

import datetime as dt
import time as _time

import jdatetime
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from recognition import checkout_errors as _auto_fix
from users.models import CheckOutError, Time


def aware(y, m, d, hh=0, mm=0, ss=0):
    return timezone.make_aware(
        dt.datetime(y, m, d, hh, mm, ss), timezone.get_current_timezone())


def jal_text(day):
    j = jdatetime.date.fromgregorian(date=day)
    return '%04d/%02d/%02d' % (j.year, j.month, j.day)


class CheckoutErrorFixturesMixin:
    """ساخت امن `CheckOutError` بدون آنکه اسکن خودکار چیزی بسازد/خراب کند."""

    def make_error(self, user, day, status=CheckOutError.STATUS_PENDING):
        auto = aware(day.year, day.month, day.day, 23, 59, 59)
        rec = Time.objects.create(user=user, date=day, time=auto, out=True)
        data = dict(
            user=user, date=day, time_record=rec, auto_out_time=auto,
        )
        if status == CheckOutError.STATUS_RESOLVED:
            # شبیه «رفع‌شدهٔ واقعی»: زمان واقعی خروج ۱۸:۳۰ ثبت شده است.
            rec.time = aware(day.year, day.month, day.day, 18, 30)
            rec.save(update_fields=['time'])
            data.update(
                status=status, resolved_by=self.boss,
                resolved_at=timezone.now(), note='زمان اصلاح شد',
            )
        return CheckOutError.objects.create(**data)

    def neutralize_autofix(self):
        """جلوی اسکن خودکار را در طول تست بگیر (تا شمارش‌ها قطعی باشد)."""
        _auto_fix._last_run_at[0] = _time.monotonic()


class CheckoutErrorFilterTests(CheckoutErrorFixturesMixin, TestCase):

    def setUp(self):
        self.boss = User.objects.create_superuser('boss', 'b@b.com', 'x')
        self.ali = User.objects.create_user(
            'ali', password='x', first_name='علی')
        self.reza = User.objects.create_user(
            'reza', password='x', first_name='رضا')
        self.url = reverse('view-checkout-errors')
        self.today = timezone.localdate()
        self.neutralize_autofix()
        self.client.force_login(self.boss)

    # ---------- دسترسی و رندر ----------

    def test_login_required(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_non_admin_redirected(self):
        self.client.force_login(self.ali)
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_filter_card_and_presets_render(self):
        self.make_error(self.ali, self.today)
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'فیلتر خطاها')
        self.assertContains(r, 'میانبر:')
        for preset in ('today', 'week', 'currentMonth', 'month'):
            self.assertContains(r, 'data-jalali-preset="%s"' % preset)
        self.assertContains(r, 'report-presets.js')

    # ---------- فیلترها ----------

    def test_search_filters_by_username(self):
        self.make_error(self.ali, self.today)
        self.make_error(self.reza, self.today - dt.timedelta(days=1))
        r = self.client.get(self.url, {'q': 'ali'})
        pending = list(r.context['pending_errors'])
        self.assertEqual([e.user.username for e in pending], ['ali'])
        self.assertEqual(r.context['pending_count'], 1)

    def test_status_pending_hides_resolved_section(self):
        self.make_error(self.ali, self.today)
        self.make_error(self.reza, self.today - dt.timedelta(days=1),
                        CheckOutError.STATUS_RESOLVED)
        r = self.client.get(self.url, {'status': 'pending'})
        self.assertTrue(r.context['show_pending'])
        self.assertFalse(r.context['show_resolved'])
        self.assertNotContains(r, 'خطاهای رفع‌شده')
        self.assertEqual(len(list(r.context['pending_errors'])), 1)

    def test_status_resolved_shows_only_resolved(self):
        self.make_error(self.ali, self.today)
        self.make_error(self.reza, self.today - dt.timedelta(days=1),
                        CheckOutError.STATUS_RESOLVED)
        r = self.client.get(self.url, {'status': 'resolved'})
        self.assertFalse(r.context['show_pending'])
        self.assertTrue(r.context['show_resolved'])
        usernames = [e.user.username for e in r.context['resolved_errors']]
        self.assertEqual(usernames, ['reza'])

    def test_date_range_filters(self):
        self.make_error(self.ali, self.today)
        self.make_error(self.reza, self.today - dt.timedelta(days=10))
        jal = jal_text(self.today)
        r = self.client.get(self.url, {'date_from': jal, 'date_to': jal})
        pending = list(r.context['pending_errors'])
        self.assertEqual([e.user.username for e in pending], ['ali'])

    def test_invalid_date_does_not_crash_and_ignores_filter(self):
        self.make_error(self.ali, self.today)
        r = self.client.get(self.url,
                            {'date_from': 'یه روزی', 'date_to': 'xx'})
        self.assertEqual(r.status_code, 200)
        # فرم نامعتبر → فیلتر تاریخ اعمال نمی‌شود ولی صفحه سالم است
        self.assertEqual(len(list(r.context['pending_errors'])), 1)

    def test_inverted_range_renders_with_error(self):
        self.make_error(self.ali, self.today)
        r = self.client.get(self.url, {
            'date_from': jal_text(self.today),
            'date_to': jal_text(self.today - dt.timedelta(days=5)),
        })
        self.assertEqual(r.status_code, 200)

    def test_junk_status_treated_as_all(self):
        self.make_error(self.ali, self.today)
        r = self.client.get(self.url, {'status': 'hack'})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.context['show_pending'])
        self.assertTrue(r.context['show_resolved'])
        self.assertEqual(r.context['status_value'], '')

    def test_empty_filtered_result_offers_reset_link(self):
        self.make_error(self.ali, self.today)
        r = self.client.get(self.url, {'q': 'kariide-nist'})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'نمایش همه خطاها')

    # ---------- صفحه‌بندی جدول رفع‌شده ----------

    def test_resolved_pagination(self):
        for i in range(16):
            self.make_error(
                self.ali, self.today - dt.timedelta(days=i),
                CheckOutError.STATUS_RESOLVED)
        r = self.client.get(self.url)
        page = r.context['resolved_errors']
        self.assertEqual(len(page), 15)
        self.assertEqual(page.paginator.num_pages, 2)
        self.assertEqual(r.context['resolved_count'], 16)

        r2 = self.client.get(self.url, {'resolved_page': '2'})
        self.assertEqual(len(r2.context['resolved_errors']), 1)
        self.assertEqual(r2.context['resolved_page'].number, 2)

    def test_pagination_link_keeps_active_filters(self):
        self.assertEqual(
            self.client.get(
                self.url, {'q': 'ali', 'resolved_page': '2'}
            ).context['filter_query'],
            'q=ali',
        )


class ReopenCheckoutErrorTests(CheckoutErrorFixturesMixin, TestCase):

    def setUp(self):
        self.boss = User.objects.create_superuser('boss', 'b@b.com', 'x')
        self.ali = User.objects.create_user('ali', password='x')
        self.today = timezone.localdate()
        self.url = reverse('view-checkout-errors')
        self.neutralize_autofix()
        self.client.force_login(self.boss)

    def test_reopen_resolved_restores_pending_and_auto_time(self):
        err = self.make_error(self.ali, self.today,
                             CheckOutError.STATUS_RESOLVED)
        self.assertNotEqual(err.time_record.time, err.auto_out_time)

        url = reverse('reopen-checkout-error', args=[err.pk])
        r = self.client.post(url, follow=True)
        self.assertEqual(r.redirect_chain[0][0], self.url)
        self.assertContains(r, 'بازگشایی شد')

        err.refresh_from_db()
        err.time_record.refresh_from_db()
        self.assertEqual(err.status, CheckOutError.STATUS_PENDING)
        self.assertIsNone(err.resolved_by)
        self.assertIsNone(err.resolved_at)
        self.assertEqual(err.time_record.time, err.auto_out_time)

    def test_reopen_pending_is_ignored_with_warning(self):
        err = self.make_error(self.ali, self.today)
        url = reverse('reopen-checkout-error', args=[err.pk])
        r = self.client.post(url, follow=True)
        self.assertContains(r, 'فقط خطای «رفع‌شده»')
        err.refresh_from_db()
        self.assertEqual(err.status, CheckOutError.STATUS_PENDING)

    def test_reopen_requires_post(self):
        err = self.make_error(self.ali, self.today,
                             CheckOutError.STATUS_RESOLVED)
        url = reverse('reopen-checkout-error', args=[err.pk])
        self.assertEqual(self.client.get(url).status_code, 405)

    def test_reopen_requires_admin(self):
        err = self.make_error(self.ali, self.today,
                             CheckOutError.STATUS_RESOLVED)
        url = reverse('reopen-checkout-error', args=[err.pk])
        self.client.force_login(self.ali)
        r = self.client.post(url)
        self.assertEqual(r.status_code, 302)
        err.refresh_from_db()
        self.assertEqual(err.status, CheckOutError.STATUS_RESOLVED)

    def test_reopen_unknown_id_404(self):
        url = reverse('reopen-checkout-error', args=[99999])
        self.assertEqual(self.client.post(url).status_code, 404)
