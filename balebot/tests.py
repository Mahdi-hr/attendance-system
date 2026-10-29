"""تست‌های اپ «بله بات».

کلاینت واقعی بله mock می‌شود تا تست‌ها به شبکه وابسته نباشند؛ ولی همان
مسیر واقعی `handlers.handle_update` اجرا می‌شود.
"""

import datetime as dt
import os

import jdatetime
from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone

from errands.models import Errand
from users.models import CheckOutError, Time, UserProfile
from users.roles import ROLE_ADMIN, ROLE_BOTH, ROLE_USER

from . import handlers
from .models import AuditLog, BaleAccount, BotConfig
from .reminders import run_reminders


User = get_user_model()


class FakeClient:
    """کلاینت بلهٔ ساختگی — پیام‌ها را برای بازرسی نگه می‌دارد."""

    def __init__(self):
        self.sent = []
        self.answers = []
        self.deleted = []
        self.documents = []
        self.photos = []
        self.edits = []

    def send_message(self, chat_id, text, reply_markup=None):
        self.sent.append({
            'chat_id': chat_id, 'text': text, 'markup': reply_markup,
        })
        return {'message_id': len(self.sent)}

    def send_document(self, chat_id, filename, content, caption=None,
                      mime_type=None):
        self.documents.append({
            'chat_id': chat_id, 'filename': filename,
            'content': content, 'caption': caption,
        })
        return {'message_id': len(self.sent) + 1}

    def send_photo(self, chat_id, filename, content, caption=None,
                   mime_type=None, reply_markup=None):
        self.photos.append({
            'chat_id': chat_id, 'filename': filename,
            'content': content, 'caption': caption,
            'markup': reply_markup,
        })
        return {'message_id': len(self.sent) + 1}

    def answer_callback_query(self, callback_query_id, text=None):
        self.answers.append((callback_query_id, text))
        return True

    def delete_message(self, chat_id, message_id):
        self.deleted.append((chat_id, message_id))
        return True

    def edit_message_text(self, chat_id, message_id, text, reply_markup=None):
        self.edits.append({
            'chat_id': chat_id, 'message_id': message_id,
            'text': text, 'markup': reply_markup,
        })
        # مثل رفتار واقعی، متن نهایی در همان جای پیام قرار می‌گیرد.
        self.sent.append({
            'chat_id': chat_id, 'text': text, 'markup': reply_markup,
            'edit': True,
        })
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
        self.assertTrue(any('در انتظار' in t for t in self.bot.texts_to(2002)))

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
        self.click('bc:all', uid=2002)
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


class ProfessionalFeatureTests(BotTestBase):
    """فاز ۱: Audit، تنظیمات، رمز، صفحه‌بندی، اکسل، آمار و صفحه‌بندی."""

    def test_audit_log_on_login(self):
        self.login('emp')
        self.assertTrue(
            AuditLog.objects.filter(actor=self.emp, action='login').exists()
        )

    def test_audit_log_on_approve(self):
        today = timezone.localdate()
        tz = timezone.get_current_timezone()
        errand = Errand.objects.create(
            user=self.emp,
            start_time=timezone.make_aware(dt.datetime.combine(today, dt.time(9, 0)), tz),
            end_time=timezone.make_aware(dt.datetime.combine(today, dt.time(10, 0)), tz),
            reason='برای Audit',
        )
        self.login('boss', uid=2002)
        self.click('err:approve:%d' % errand.pk, uid=2002)
        self.assertTrue(
            AuditLog.objects.filter(action='errand_approve', target_id=str(errand.pk)).exists()
        )

    def test_settings_toggle(self):
        self.login('emp')
        self.click('settings')
        self.assertTrue(self.account().notify_errand)

        self.click('settings:toggle:errand')
        self.assertFalse(self.account().notify_errand)

        self.click('settings:toggle:errand')
        self.assertTrue(self.account().notify_errand)

    def test_password_change_flow(self):
        self.login('emp')
        self.click('settings:password')
        self.send('Secret12345')
        self.send('NewSecret123')
        self.send('NewSecret123')

        self.emp.refresh_from_db()
        self.assertTrue(self.emp.check_password('NewSecret123'))
        self.assertTrue(AuditLog.objects.filter(action='password_change').exists())
        self.assertGreaterEqual(len(self.bot.deleted), 3)

    def test_password_change_wrong_current(self):
        self.login('emp')
        self.click('settings:password')
        self.send('wrong')
        self.assertIn('اشتباه', self.bot.last_text)
        self.emp.refresh_from_db()
        self.assertTrue(self.emp.check_password('Secret12345'))

    def test_week_report(self):
        self.login('emp')
        self.click('rep:week')
        self.assertIn('۷ روز', self.bot.last_text)

    def test_excel_export(self):
        self.login('emp')
        self.click('rep:excel')
        self.assertEqual(len(self.bot.documents), 1)
        doc = self.bot.documents[0]
        self.assertTrue(doc['filename'].endswith('.xlsx'))
        self.assertTrue(doc['content'].startswith(b'PK'))

    def test_errand_pagination(self):
        today = timezone.localdate()
        tz = timezone.get_current_timezone()
        for hour in range(7):
            Errand.objects.create(
                user=self.emp,
                start_time=timezone.make_aware(
                    dt.datetime.combine(today, dt.time(8 + hour, 0)), tz),
                end_time=timezone.make_aware(
                    dt.datetime.combine(today, dt.time(8 + hour, 30)), tz),
                reason='مأموریت %d' % hour,
                status=Errand.STATUS_CANCELLED,
            )
        self.login('emp')
        self.click('err:mine')
        markups = [m['markup'] for m in self.bot.sent if m['markup']]
        self.assertTrue(any('err:mine:2' in str(m) for m in markups))

        self.click('err:mine:2')
        self.assertTrue(any('صفحهٔ ۲' in t for t in self.bot.texts_to(1001)))

    def test_admin_stats(self):
        self.login('boss', uid=2002)
        self.click('adm:stats', uid=2002)
        self.assertIn('آمار ربات', self.bot.last_text)

    def test_admin_audit_view(self):
        AuditLog.objects.create(actor=self.admin, action='test_action', details='تست')
        self.login('boss', uid=2002)
        self.click('adm:audit:1', uid=2002)
        self.assertIn('ردپای اقدامات', self.bot.last_text)

    def test_approve_all(self):
        today = timezone.localdate()
        tz = timezone.get_current_timezone()
        for i in range(2):
            Errand.objects.create(
                user=self.emp,
                start_time=timezone.make_aware(
                    dt.datetime.combine(today, dt.time(9 + i, 0)), tz),
                end_time=timezone.make_aware(
                    dt.datetime.combine(today, dt.time(9 + i, 30)), tz),
                reason='گروهی %d' % i,
            )
        self.login('boss', uid=2002)
        self.click('adm:approve_all', uid=2002)
        self.assertIn('تأیید شوند', self.bot.last_text)

        self.click('adm:approve_all:yes', uid=2002)
        self.assertEqual(
            Errand.objects.filter(user=self.emp, status=Errand.STATUS_APPROVED).count(),
            2,
        )
        self.assertTrue(AuditLog.objects.filter(action='errand_approve_all').exists())

    def test_bot_disabled_ignores_messages(self):
        config = BotConfig.get_solo()
        config.bot_enabled = False
        config.save()

        before = len(self.bot.sent)
        self.login('emp')
        self.assertEqual(len(self.bot.sent), before)

        config.bot_enabled = True
        config.save()

    def test_rate_limit(self):
        self.login('emp')
        account = self.account()
        account.rate_window_start = timezone.now()
        account.rate_count = 30
        account.save()

        before = len(self.bot.sent)
        self.send('/menu')
        self.assertEqual(len(self.bot.sent), before)

    def test_reply_keyboard_action(self):
        self.login('emp')
        self.send('📊 وضعیت امروز')
        self.assertIn('وضعیت امروز', self.bot.last_text)

    def test_admin_reply_keyboard_action(self):
        self.login('boss', uid=2002)
        self.send('📊 داشبورد', uid=2002)
        self.assertIn('داشبورد امروز', self.bot.last_text)

    def test_pending_pagination(self):
        today = timezone.localdate()
        tz = timezone.get_current_timezone()
        for i in range(6):
            Errand.objects.create(
                user=self.emp,
                start_time=timezone.make_aware(
                    dt.datetime.combine(today, dt.time(9 + i, 0)), tz),
                end_time=timezone.make_aware(
                    dt.datetime.combine(today, dt.time(9 + i, 30)), tz),
                reason='در انتظار %d' % i,
            )
        self.login('boss', uid=2002)
        self.click('adm:pending', uid=2002)
        markups = [m['markup'] for m in self.bot.sent if m['markup']]
        self.assertTrue(any('adm:pending:2' in str(m) for m in markups))

        self.click('adm:pending:2', uid=2002)
        self.assertTrue(any('صفحهٔ ۲' in t for t in self.bot.texts_to(2002)))


class ReminderTests(BotTestBase):
    """یادآورهای خودکار (خروج، مأموریت نزدیک، صف تأیید)."""

    def test_checkout_reminder_once(self):
        self.login('emp')
        now = timezone.now()
        today = timezone.localdate(now)
        Time.objects.create(
            user=self.emp, date=today,
            time=now - dt.timedelta(hours=6), out=False,
        )
        config = BotConfig.get_solo()
        config.work_end_time = (
            timezone.localtime(now) - dt.timedelta(hours=2)
        ).time()
        config.checkout_reminder_after_minutes = 0
        config.save()

        stats = run_reminders(self.bot, now=now)
        self.assertEqual(stats['checkout'], 1)
        self.assertTrue(any('یادآور خروج' in t for t in self.bot.texts_to(1001)))

        stats2 = run_reminders(self.bot, now=now)
        self.assertEqual(stats2['checkout'], 0)

    def test_upcoming_errand_reminder_once(self):
        self.login('emp')
        now = timezone.now()
        Errand.objects.create(
            user=self.emp,
            start_time=now + dt.timedelta(minutes=10),
            end_time=now + dt.timedelta(hours=1),
            reason='بانک',
            status=Errand.STATUS_APPROVED,
        )
        config = BotConfig.get_solo()
        config.upcoming_errand_minutes = 15
        config.save()

        stats = run_reminders(self.bot, now=now)
        self.assertEqual(stats['upcoming'], 1)
        self.assertTrue(any('یادآور مأموریت' in t for t in self.bot.texts_to(1001)))

        stats2 = run_reminders(self.bot, now=now)
        self.assertEqual(stats2['upcoming'], 0)

    def test_pending_digest_once(self):
        self.login('boss', uid=2002)
        now = timezone.now()
        Errand.objects.create(
            user=self.emp,
            start_time=now + dt.timedelta(hours=1),
            end_time=now + dt.timedelta(hours=2),
            reason='در انتظار',
        )
        config = BotConfig.get_solo()
        config.pending_digest_time = timezone.localtime(now).time()
        config.save()

        stats = run_reminders(self.bot, now=now)
        self.assertEqual(stats['pending'], 1)
        self.assertTrue(any('یادآور صف تأیید' in t for t in self.bot.texts_to(2002)))

        stats2 = run_reminders(self.bot, now=now)
        self.assertEqual(stats2['pending'], 0)


class PhaseThreeTests(BotTestBase):
    """فاز ۳: پروندهٔ ۳۶۰، اصلاح حضور، گزارش تیمی و پیام هدفمند."""

    def _pending_errand(self, reason='در انتظار'):
        today = timezone.localdate()
        tz = timezone.get_current_timezone()
        return Errand.objects.create(
            user=self.emp,
            start_time=timezone.make_aware(
                dt.datetime.combine(today, dt.time(9, 0)), tz),
            end_time=timezone.make_aware(
                dt.datetime.combine(today, dt.time(10, 0)), tz),
            reason=reason,
        )

    def test_user_360(self):
        self.login('boss', uid=2002)
        self.click('usr:%d' % self.emp.pk, uid=2002)
        self.assertIn('کارکرد', self.bot.last_text)
        self.assertIn('مأموریت', self.bot.last_text)

    def test_attendance_correction(self):
        now = timezone.now()
        record = Time.objects.create(
            user=self.emp, date=timezone.localdate(now),
            time=now, out=False,
        )
        self.login('boss', uid=2002)
        self.click('usr:time:%d' % self.emp.pk, uid=2002)
        self.click('usr:timeedit:%d' % record.pk, uid=2002)
        self.send(jalali_today(8, 30), uid=2002)

        record.refresh_from_db()
        self.assertEqual(
            timezone.localtime(record.time).strftime('%H:%M'), '08:30',
        )
        self.assertTrue(
            AuditLog.objects.filter(action='attendance_edit').exists()
        )

    def test_team_report(self):
        self.login('boss', uid=2002)
        self.click('adm:report', uid=2002)
        self.assertIn('گزارش تیمی', self.bot.last_text)

    def test_team_report_excel(self):
        self.login('boss', uid=2002)
        self.click('adm:report:excel', uid=2002)
        self.assertEqual(len(self.bot.documents), 1)
        self.assertTrue(self.bot.documents[0]['filename'].endswith('.xlsx'))

    def test_analytics(self):
        self.login('boss', uid=2002)
        self.click('adm:analytics', uid=2002)
        self.assertIn('تحلیل ماه', self.bot.last_text)

    def test_targeted_broadcast(self):
        self.login('emp', uid=1001)
        self.login('boss', uid=2002)
        self.click('adm:broadcast', uid=2002)
        self.click('bc:user', uid=2002)
        self.send('emp', uid=2002)
        self.send('فقط برای شما', uid=2002)
        self.assertTrue(any('فقط برای شما' in t for t in self.bot.texts_to(1001)))


class EditErrandTests(BotTestBase):
    """فاز ۲: ویرایش مأموریت در انتظار و قالب‌های سریع."""

    def _pending_errand(self, reason='قدیمی'):
        today = timezone.localdate()
        tz = timezone.get_current_timezone()
        return Errand.objects.create(
            user=self.emp,
            start_time=timezone.make_aware(
                dt.datetime.combine(today, dt.time(9, 0)), tz),
            end_time=timezone.make_aware(
                dt.datetime.combine(today, dt.time(10, 0)), tz),
            reason=reason,
        )

    def test_edit_reason(self):
        errand = self._pending_errand()
        self.login('emp')
        self.click('err:edit:%d' % errand.pk)
        self.click('err:edit:%d:reason' % errand.pk)
        self.send('دلیل جدید')

        errand.refresh_from_db()
        self.assertEqual(errand.reason, 'دلیل جدید')
        self.assertTrue(AuditLog.objects.filter(action='errand_edit').exists())

    def test_edit_end_before_start_rejected(self):
        errand = self._pending_errand()
        self.login('emp')
        self.click('err:edit:%d' % errand.pk)
        self.click('err:edit:%d:end' % errand.pk)
        self.send(jalali_today(8, 0))

        errand.refresh_from_db()
        self.assertEqual(
            timezone.localtime(errand.end_time).strftime('%H:%M'), '10:00',
        )
        self.assertIn('بعد از شروع', self.bot.last_text)

    def test_non_pending_errand_not_editable(self):
        errand = self._pending_errand()
        errand.status = Errand.STATUS_APPROVED
        errand.save(update_fields=['status'])
        self.login('emp')
        self.click('err:edit:%d' % errand.pk)
        self.assertIn('در انتظار', self.bot.last_text)

    def test_quick_reason_template(self):
        self._pending_errand(reason='بانک')
        self.login('emp')
        self.click('err:new')
        self.send(jalali_today(9))
        self.send(jalali_today(12))
        self.click('err:tpl:reason:0')
        self.assertIn('مقصد', self.bot.last_text)

        self.click('err:skip:destination')
        self.click('err:skip:contact')
        created = Errand.objects.filter(user=self.emp).order_by('-pk').first()
        self.assertEqual(created.reason, 'بانک')


class SnapshotBotTests(BotTestBase):
    """فاز ۴: بازبینی عکس لحظه از داخل ربات."""

    def setUp(self):
        super().setUp()
        import shutil
        import tempfile
        from django.test import override_settings

        self.tmp = tempfile.mkdtemp(prefix='bale-snap-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.override = override_settings(BASE_DIR=self.tmp)
        self.override.enable()
        self.addCleanup(self.override.disable)

    def _snap(self, with_file=True):
        from users.models import AttendanceSnapshot
        from users.snapshots import snapshots_root

        snap = AttendanceSnapshot.objects.create(
            user=self.emp, event='in', photo='emp/test.jpg',
        )
        if with_file:
            path = os.path.join(snapshots_root(), snap.photo)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'wb') as fh:
                fh.write(b'\xff\xd8\xff\xd9')
        return snap

    def test_view_and_approve_snapshot(self):
        from users.models import AttendanceSnapshot

        snap = self._snap()
        self.login('boss', uid=2002)
        self.click('adm:snapshots', uid=2002)
        self.click('snap:%d' % snap.pk, uid=2002)
        self.assertEqual(len(self.bot.photos), 1)
        self.assertIn('تأیید', str(self.bot.photos[0]['markup']))

        self.click('snap:ok:%d' % snap.pk, uid=2002)
        snap.refresh_from_db()
        self.assertEqual(snap.review_status, AttendanceSnapshot.REVIEW_OK)
        self.assertEqual(snap.reviewed_by, self.admin)
        self.assertTrue(
            AuditLog.objects.filter(action='snapshot_review').exists()
        )

    def test_reject_requires_note(self):
        from users.models import AttendanceSnapshot

        snap = self._snap()
        self.login('boss', uid=2002)
        self.click('snap:no:%d' % snap.pk, uid=2002)
        self.send('   ', uid=2002)
        self.assertIn('الزامی', self.bot.last_text)

        self.send('تشخیص اشتباه', uid=2002)
        snap.refresh_from_db()
        self.assertEqual(snap.review_status, AttendanceSnapshot.REVIEW_REJECTED)
        self.assertEqual(snap.review_note, 'تشخیص اشتباه')

    def test_missing_file_is_not_sent(self):
        snap = self._snap(with_file=False)
        self.login('boss', uid=2002)
        self.click('snap:%d' % snap.pk, uid=2002)
        self.assertEqual(len(self.bot.photos), 0)
        self.assertIn('موجود نیست', self.bot.last_text)


class WebhookTests(BotTestBase):
    """فاز ۴: وبهوک اختیاری (فقط با سکرت درست)."""

    def test_wrong_secret_forbidden(self):
        import json
        from django.test import override_settings

        with override_settings(BALE_WEBHOOK_SECRET='s3cret'):
            response = self.client.post(
                '/balebot/webhook/wrong/',
                data=json.dumps(message('/start')),
                content_type='application/json',
            )
        self.assertEqual(response.status_code, 403)

    def test_valid_webhook_processes_update(self):
        import json
        from unittest.mock import patch
        from django.test import override_settings

        with override_settings(BALE_WEBHOOK_SECRET='s3cret'):
            with patch('balebot.webhook.BaleClient', return_value=self.bot):
                response = self.client.post(
                    '/balebot/webhook/s3cret/',
                    data=json.dumps(message('/start', uid=7007)),
                    content_type='application/json',
                )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            BaleAccount.objects.filter(bale_user_id=7007).exists()
        )


class AnomalyAndScheduleTests(BotTestBase):
    """فاز ۵: هشدار ناهنجاری + فاز ۳: پیام زمان‌بندی‌شده."""

    def test_anomaly_alert_for_long_open_session(self):
        self.login('emp', uid=1001)
        self.login('boss', uid=2002)
        now = timezone.now()
        Time.objects.create(
            user=self.emp,
            date=timezone.localdate(now - dt.timedelta(days=1)),
            time=now - dt.timedelta(hours=20),
            out=False,
        )

        stats = run_reminders(self.bot, now=now)
        self.assertEqual(stats['anomalies'], 1)
        self.assertTrue(any(
            'هشدار ناهنجاری' in t for t in self.bot.texts_to(2002)
        ))

        stats2 = run_reminders(self.bot, now=now)
        self.assertEqual(stats2['anomalies'], 0)

    def test_scheduled_message_is_sent_once(self):
        from .models import ScheduledMessage
        from .services import send_scheduled_due

        self.login('emp', uid=1001)
        msg = ScheduledMessage.objects.create(
            text='یادآوری آزمایشی',
            audience='all',
            send_at=timezone.now() - dt.timedelta(minutes=1),
        )

        self.assertEqual(send_scheduled_due(self.bot), 1)
        self.assertTrue(any(
            'یادآوری آزمایشی' in t for t in self.bot.texts_to(1001)
        ))
        msg.refresh_from_db()
        self.assertIsNotNone(msg.sent_at)
        self.assertEqual(send_scheduled_due(self.bot), 0)

    def test_schedule_flow_creates_message(self):
        from .models import ScheduledMessage

        tomorrow = timezone.localdate() + dt.timedelta(days=1)
        jalali = jdatetime.date.fromgregorian(date=tomorrow).strftime('%Y/%m/%d')

        self.login('boss', uid=2002)
        self.click('adm:schedule', uid=2002)
        self.send('جلسهٔ فردا', uid=2002)
        self.send('%s 09:00' % jalali, uid=2002)
        self.click('sched:admins', uid=2002)

        msg = ScheduledMessage.objects.first()
        self.assertIsNotNone(msg)
        self.assertEqual(msg.text, 'جلسهٔ فردا')
        self.assertEqual(msg.audience, 'admins')
        self.assertEqual(msg.created_by, self.admin)


class InlineNavigationTests(BotTestBase):
    """رابط کاربری فقط inline: دسته‌بندی، ویرایش همان پیام، بدون کیبورد ثابت."""

    def test_no_persistent_keyboard_is_sent_on_login(self):
        self.login('emp')
        for message in self.bot.sent:
            markup = message.get('markup') or {}
            self.assertNotIn('keyboard', markup)

    def test_main_menu_is_categories_not_dozens_of_buttons(self):
        self.login('emp')
        markup = str(self.bot.last_markup)
        self.assertIn('menu:work', markup)
        self.assertIn('menu:reports', markup)
        self.assertIn('menu:settings', markup)
        # دکمه‌های عملیات مستقیم نباید در منوی اصلی شلوغ شوند.
        self.assertNotIn('err:new', markup)

    def test_work_menu_has_expected_actions_and_back(self):
        self.login('emp')
        self.click('menu:work')
        text = self.bot.last_text
        markup = str(self.bot.last_markup)
        self.assertIn('کارهای من', text)
        self.assertIn('err:new', markup)
        self.assertIn('err:mine', markup)
        self.assertIn('menu:main', markup)

    def test_reports_menu_admin_has_team_and_analytics(self):
        self.login('boss', uid=2002)
        self.click('menu:reports', uid=2002)
        markup = str(self.bot.last_markup)
        self.assertIn('adm:report', markup)
        self.assertIn('adm:analytics', markup)
        self.assertIn('rep:excel', markup)

    def test_admin_hub_has_tools_and_back(self):
        self.login('boss', uid=2002)
        self.click('menu:admin', uid=2002)
        text = self.bot.last_text
        markup = str(self.bot.last_markup)
        self.assertIn('مدیریت', text)
        self.assertIn('adm:pending', markup)
        self.assertIn('adm:users', markup)
        self.assertIn('adm:snapshots', markup)
        self.assertIn('menu:main', markup)

    def test_menu_navigation_edits_the_same_message(self):
        self.login('boss', uid=2002)
        self.click('menu:admin', uid=2002)
        self.assertTrue(self.bot.edits)
        self.assertIn('مدیریت', self.bot.edits[-1]['text'])

        self.click('menu:main', uid=2002)
        self.assertIn('منوی اصلی', self.bot.edits[-1]['text'])
        self.assertEqual(self.bot.edits[-1]['message_id'], 1)

    def test_settings_menu_has_back(self):
        self.login('emp')
        self.click('menu:settings')
        markup = str(self.bot.last_markup)
        self.assertIn('settings:toggle:errand', markup)
        self.assertIn('settings:password', markup)
        self.assertIn('menu:main', markup)
