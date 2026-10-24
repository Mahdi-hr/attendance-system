"""تست‌های اپ «بله بات».

کلاینت واقعی بله mock می‌شود تا تست‌ها به شبکه وابسته نباشند؛ ولی همان
مسیر واقعی `handlers.handle_update` اجرا می‌شود.
"""

import datetime as dt

import jdatetime
from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone

from errands.models import Errand
from users.models import CheckOutError, Time, UserProfile
from users.roles import ROLE_ADMIN, ROLE_BOTH, ROLE_USER

from . import handlers
from .models import BaleAccount


User = get_user_model()


class FakeClient:
    """کلاینت بلهٔ ساختگی — پیام‌ها را برای بازرسی نگه می‌دارد."""

    def __init__(self):
        self.sent = []
        self.answers = []
        self.deleted = []

    def send_message(self, chat_id, text, reply_markup=None):
        self.sent.append({
            'chat_id': chat_id, 'text': text, 'markup': reply_markup,
        })
        return {'message_id': len(self.sent)}

    def answer_callback_query(self, callback_query_id, text=None):
        self.answers.append((callback_query_id, text))
        return True

    def delete_message(self, chat_id, message_id):
        self.deleted.append((chat_id, message_id))
        return True

    @property
    def last_text(self):
        return self.sent[-1]['text'] if self.sent else ''

    @property
    def last_markup(self):
        return self.sent[-1]['markup'] if self.sent else None

    def texts_to(self, chat_id):
        return [m['text'] for m in self.sent if m['chat_id'] == chat_id]


def message(text, uid=1001, mid=1):
    return {
        'update_id': 1,
        'message': {
            'message_id': mid,
            'from': {'id': uid, 'first_name': 'تست', 'username': 'tester'},
            'chat': {'id': uid, 'type': 'private'},
            'text': text,
        },
    }


def callback(data, uid=1001):
    return {
        'update_id': 2,
        'callback_query': {
            'id': 'cb-1',
            'from': {'id': uid, 'first_name': 'تست', 'username': 'tester'},
            'message': {'message_id': 1, 'chat': {'id': uid}},
            'data': data,
        },
    }


def jalali_today(hour, minute=0):
    today = timezone.localdate()
    j = jdatetime.date.fromgregorian(date=today)
    return '%s %02d:%02d' % (j.strftime('%Y/%m/%d'), hour, minute)


class BotTestBase(TestCase):
    def setUp(self):
        self.bot = FakeClient()

        self.emp = User.objects.create_user(
            'emp', password='Secret12345', first_name='کارمند', last_name='تست',
        )
        UserProfile.objects.update_or_create(
            user=self.emp, defaults={'role': ROLE_USER},
        )

        self.admin = User.objects.create_superuser(
            'boss', email='boss@example.com', password='Secret12345',
        )

        self.both = User.objects.create_user(
            'both', password='Secret12345',
        )
        UserProfile.objects.update_or_create(
            user=self.both, defaults={'role': ROLE_BOTH},
        )

    # ---------- کمک‌کننده‌ها ----------
    def send(self, text, uid=1001):
        handlers.handle_update(message(text, uid), self.bot)

    def click(self, data, uid=1001):
        handlers.handle_update(callback(data, uid), self.bot)

    def login(self, username, password='Secret12345', uid=1001):
        self.send('/start', uid)
        self.send(username, uid)
        self.send(password, uid)

    def account(self, uid=1001):
        return BaleAccount.objects.get(bale_user_id=uid)


class StartAndLoginTests(BotTestBase):
    def test_start_asks_username(self):
        self.send('/start')
        self.assertIn('نام کاربری', self.bot.last_text)

    def test_login_success_links_account(self):
        self.login('emp')
        account = self.account()
        self.assertEqual(account.django_user, self.emp)
        self.assertIsNotNone(account.linked_at)
        self.assertIn('منوی اصلی', self.bot.last_text)
        self.assertIn('کاربر', self.bot.last_text)

    def test_wrong_password_does_not_link(self):
        self.send('/start')
        self.send('emp')
        self.send('wrong-password')
        account = self.account()
        self.assertIsNone(account.django_user)
        self.assertIn('اشتباه', self.bot.last_text)

    def test_password_message_is_deleted(self):
        self.send('/start')
        self.send('emp')
        self.send('Secret12345')
        self.assertTrue(self.bot.deleted)

    def test_lock_after_five_failures(self):
        self.send('/start')
        self.send('emp')
        for _ in range(5):
            self.send('bad-password')
        account = self.account()
        self.assertIsNotNone(account.locked_until)
        self.assertIn('صبر', self.bot.last_text)

    def test_unknown_command(self):
        self.login('emp')
        self.send('/nope')
        self.assertIn('ناشناخته', self.bot.last_text)

    def test_logout(self):
        self.login('emp')
        self.click('logout')
        account = self.account()
        self.assertIsNone(account.django_user)
        self.assertEqual(account.state, 'login_username')
        self.assertIn('خارج شدید', self.bot.last_text)


class RoleSwitchTests(BotTestBase):
    def test_both_user_can_switch_to_admin_and_back(self):
        self.login('both', uid=3003)
        account = self.account(3003)
        self.assertEqual(account.active_role, ROLE_USER)

        self.click('role:toggle', uid=3003)
        account.refresh_from_db()
        self.assertEqual(account.active_role, ROLE_ADMIN)
        self.assertIn('مدیر', self.bot.last_text)

        self.click('role:toggle', uid=3003)
        account.refresh_from_db()
        self.assertEqual(account.active_role, ROLE_USER)
        self.assertIn('کاربر', self.bot.last_text)

    def test_pure_admin_has_no_role_switch(self):
        self.login('boss', uid=2002)
        self.assertNotIn('role:toggle', str(self.bot.last_markup))

    def test_pure_user_cannot_switch(self):
        self.login('emp')
        self.click('role:toggle')
        self.assertIn('نقش دوگانه', self.bot.last_text)

    def test_normal_user_cannot_open_admin_section(self):
        self.login('emp')
        self.click('adm:users')
        self.assertIn('فقط برای مدیر', self.bot.last_text)

    def test_both_user_admin_section_requires_admin_mode(self):
        self.login('both', uid=3003)
        self.click('adm:users', uid=3003)
        self.assertIn('فقط برای مدیر', self.bot.last_text)

        self.click('role:toggle', uid=3003)
        self.click('adm:users', uid=3003)
        self.assertIn('مدیریت کاربران', self.bot.last_text)


class EmployeeFeatureTests(BotTestBase):
    def test_status(self):
        self.login('emp')
        self.click('status')
        self.assertIn('وضعیت امروز', self.bot.last_text)

    def test_month_report(self):
        self.login('emp')
        self.click('rep:month')
        self.assertIn('گزارش ماه', self.bot.last_text)

    def test_create_errand_flow(self):
        self.login('emp')
        self.click('err:new')
        self.send(jalali_today(9))
        self.send(jalali_today(12))
        self.send('مراجعه به بانک')
        self.click('err:skip:destination')
        self.click('err:skip:contact')

        errand = Errand.objects.get(user=self.emp, reason='مراجعه به بانک')
        self.assertEqual(errand.status, Errand.STATUS_PENDING)
        self.assertIn('ثبت شد', self.bot.last_text)

    def test_errand_end_before_start_is_rejected(self):
        self.login('emp')
        self.click('err:new')
        self.send(jalali_today(12))
        self.send(jalali_today(9))
        self.assertIn('بعد از شروع', self.bot.last_text)
        self.assertFalse(Errand.objects.filter(user=self.emp).exists())

    def test_errand_overlap_is_rejected(self):
        today = timezone.localdate()
        tz = timezone.get_current_timezone()

        def aware(hour):
            return timezone.make_aware(
                dt.datetime.combine(today, dt.time(hour, 0)), tz,
            )

        Errand.objects.create(
            user=self.emp,
            start_time=aware(9),
            end_time=aware(13),
            reason='مأموریت قبلی',
            status=Errand.STATUS_APPROVED,
        )

        self.login('emp')
        self.click('err:new')
        self.send(jalali_today(10))
        self.send(jalali_today(11))
        self.send('مأموریت جدید')
        self.click('err:skip:destination')
        self.click('err:skip:contact')

        self.assertEqual(Errand.objects.filter(user=self.emp).count(), 1)
        self.assertIn('همپوشانی', self.bot.last_text)

    def test_cancel_pending_errand(self):
        today = timezone.localdate()
        tz = timezone.get_current_timezone()
        errand = Errand.objects.create(
            user=self.emp,
            start_time=timezone.make_aware(dt.datetime.combine(today, dt.time(9, 0)), tz),
            end_time=timezone.make_aware(dt.datetime.combine(today, dt.time(10, 0)), tz),
            reason='قابل لغو',
        )
        self.login('emp')
        self.click('err:cancel:%d' % errand.pk)
        errand.refresh_from_db()
        self.assertEqual(errand.status, Errand.STATUS_CANCELLED)

    def test_my_errands_list(self):
        self.login('emp')
        self.click('err:mine')
        self.assertIn('مأموریتی ثبت نکرده', self.bot.last_text)


class AdminFeatureTests(BotTestBase):
    def _make_pending(self):
        today = timezone.localdate()
        tz = timezone.get_current_timezone()
        return Errand.objects.create(
            user=self.emp,
            start_time=timezone.make_aware(dt.datetime.combine(today, dt.time(9, 0)), tz),
            end_time=timezone.make_aware(dt.datetime.combine(today, dt.time(10, 0)), tz),
            reason='در انتظار',
        )

    def test_admin_approve_and_notify_employee(self):
        self.login('emp', uid=1001)
        self.login('boss', uid=2002)
        errand = self._make_pending()

        self.click('err:approve:%d' % errand.pk, uid=2002)
        errand.refresh_from_db()
        self.assertEqual(errand.status, Errand.STATUS_APPROVED)
        self.assertTrue(any('تأیید شد' in t for t in self.bot.texts_to(1001)))

    def test_admin_reject_requires_reason(self):
        self.login('boss', uid=2002)
        errand = self._make_pending()

        self.click('err:reject:%d' % errand.pk, uid=2002)
        self.send('   ', uid=2002)
        self.assertIn('الزامی', self.bot.last_text)

        self.send('دلیل تست رد', uid=2002)
        errand.refresh_from_db()
        self.assertEqual(errand.status, Errand.STATUS_REJECTED)
        self.assertEqual(errand.review_note, 'دلیل تست رد')

    def test_admin_today_dashboard(self):
        self.login('boss', uid=2002)
        self.click('adm:today', uid=2002)
        self.assertIn('داشبورد امروز', self.bot.last_text)

    def test_admin_pending_list(self):
        self.login('boss', uid=2002)
        self._make_pending()
        self.click('adm:pending', uid=2002)
        self.assertTrue(any('در انتظار بررسی' in t for t in self.bot.texts_to(2002)))

    def test_resolve_checkout_error(self):
        today = timezone.localdate()
        time = timezone.now()
        record = Time.objects.create(user=self.emp, date=today, time=time)
        error = CheckOutError.objects.create(
            user=self.emp, date=today, time_record=record,
            auto_out_time=time, status=CheckOutError.STATUS_PENDING,
        )
        self.login('boss', uid=2002)
        self.click('cerr:resolve:%d' % error.pk, uid=2002)
        error.refresh_from_db()
        self.assertEqual(error.status, CheckOutError.STATUS_RESOLVED)
        self.assertEqual(error.resolved_by, self.admin)

    def test_user_search(self):
        self.login('boss', uid=2002)
        self.click('usr:search', uid=2002)
        self.send('emp', uid=2002)
        self.assertTrue(any('emp' in t for t in self.bot.texts_to(2002)))

    def test_reset_password_shows_new_password(self):
        self.login('boss', uid=2002)
        self.click('usr:reset:%d' % self.emp.pk, uid=2002)
        text = self.bot.last_text
        self.assertIn('رمز عبور', text)
        new_password = text.split('`')[1]
        self.emp.refresh_from_db()
        self.assertTrue(self.emp.check_password(new_password))

    def test_change_role(self):
        self.login('boss', uid=2002)
        self.click('usr:setrole:%d:admin' % self.emp.pk, uid=2002)
        profile = UserProfile.objects.get(user=self.emp)
        self.assertEqual(profile.role, ROLE_ADMIN)

    def test_cannot_change_superuser_role(self):
        self.login('boss', uid=2002)
        self.click('usr:setrole:%d:user' % self.admin.pk, uid=2002)
        self.assertIn('قابل تغییر نیست', self.bot.last_text)

    def test_create_new_user(self):
        self.login('boss', uid=2002)
        self.click('usr:new', uid=2002)
        self.send('newbie', uid=2002)
        self.click('usr:newskip:name', uid=2002)
        self.click('usr:newrole:user', uid=2002)

        user = User.objects.get(username='newbie')
        self.assertTrue(user.is_active)
        profile = UserProfile.objects.get(user=user)
        self.assertEqual(profile.role, ROLE_USER)
        self.assertIn('کاربر ساخته شد', self.bot.last_text)

    def test_broadcast_reaches_linked_employee(self):
        self.login('emp', uid=1001)
        self.login('boss', uid=2002)
        self.click('adm:broadcast', uid=2002)
        self.send('جلسهٔ فردا ساعت ۹', uid=2002)

        self.assertTrue(any(
            'پیام از مدیریت' in t and 'جلسهٔ فردا' in t
            for t in self.bot.texts_to(1001)
        ))
        self.assertIn('ارسال شد', self.bot.last_text)

    def test_admin_cannot_disable_self(self):
        self.login('boss', uid=2002)
        self.click('usr:toggle:%d' % self.admin.pk, uid=2002)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)
        self.assertIn('غیرفعال', self.bot.last_text)


class ModelTests(BotTestBase):
    def test_bale_user_id_is_unique(self):
        BaleAccount.objects.create(bale_user_id=999, chat_id=999)
        with self.assertRaises(IntegrityError):
            BaleAccount.objects.create(bale_user_id=999, chat_id=999)

    def test_account_str(self):
        self.login('emp')
        self.assertIn('emp', str(self.account()))
