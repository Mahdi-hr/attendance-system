"""تست‌های پنل ادمین سفارشی (`adminpanel`).

این تست‌ها چهار چیز را قفل می‌کنند:
  ۱. فقط ابرکاربر به /admin/ و ابزارها دسترسی دارد.
  ۲. همهٔ صفحه‌های پنل بدون خطای قالب رندر می‌شوند.
  ۳. ابزارهای حساس (SQL، پشتیبان، ترمیم) قواعد ایمنی‌شان را رعایت می‌کنند.
  ۴. عملیات گروهیِ سفارشی (دلیل خروج، ریست رمز، خروجی اکسل) کار می‌کنند.
"""
import datetime as dt
import os
import shutil
import sqlite3
import tempfile

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.admin.models import LogEntry, ADDITION
from django.contrib.sessions.models import Session
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from users.models import ExitReason, Time, UserProfile

from . import dbtools, diagnostics, utils

User = get_user_model()

# صفحه‌هایی که باید همیشه ۲۰۰ برگردانند
TOOL_PAGES = [
    'admin:adminpanel-health',
    'admin:adminpanel-backup',
    'admin:adminpanel-sql',
    'admin:adminpanel-logs',
    'admin:adminpanel-users-create',
    'admin:adminpanel-users-bulk',
    'admin:adminpanel-attendance',
    'admin:adminpanel-snapshots',
    'admin:adminpanel-sessions',
    'admin:adminpanel-rename',
]

MODEL_PAGES = [
    'admin:auth_user_changelist',
    'admin:auth_group_changelist',
    'admin:admin_logentry_changelist',
    'admin:sessions_session_changelist',
    'admin:users_time_changelist',
    'admin:users_present_changelist',
    'admin:users_break_changelist',
    'admin:users_checkouterror_changelist',
    'admin:users_userprofile_changelist',
    'admin:users_attendancesnapshot_changelist',
    'admin:users_exitreason_changelist',
    'admin:errands_errand_changelist',
]


class SuperuserMixin:
    """ساخت ابرکاربر و کلاینت وارد‌شده."""

    @classmethod
    def setUpTestData(cls):
        cls.superuser = User.objects.create_superuser(
            username='ap_superuser', password='ApPass!12345')
        cls.staff_user = User.objects.create_user(
            username='ap_staff', password='ApPass!12345',
            is_staff=True, is_active=True)
        cls.plain_user = User.objects.create_user(
            username='ap_plain', password='ApPass!12345')

    def login_superuser(self):
        c = Client()
        c.force_login(self.superuser)
        return c


# ============================================================
# ۱) کنترل دسترسی
# ============================================================
class AccessControlTests(SuperuserMixin, TestCase):
    def test_superuser_sees_dashboard(self):
        r = self.login_superuser().get(reverse('admin:index'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'داشبورد')

    def test_staff_user_is_redirected_to_login(self):
        c = Client()
        c.force_login(self.staff_user)
        for url in (reverse('admin:index'),
                    reverse('admin:adminpanel-health'),
                    reverse('admin:auth_user_changelist')):
            r = c.get(url, follow=False)
            self.assertEqual(r.status_code, 302, url)
            self.assertIn('login', r['Location'], url)

    def test_plain_user_is_redirected_to_login(self):
        c = Client()
        c.force_login(self.plain_user)
        r = c.get(reverse('admin:index'), follow=False)
        self.assertEqual(r.status_code, 302)
        self.assertIn('login', r['Location'])

    def test_anonymous_is_redirected_to_login(self):
        r = Client().get(reverse('admin:index'), follow=False)
        self.assertEqual(r.status_code, 302)
        self.assertIn('login', r['Location'])

    def test_admin_site_class_is_patched(self):
        from adminpanel.site import KianAdminSite
        self.assertIsInstance(admin.site, KianAdminSite)

    def test_only_superuser_passes_permission(self):
        from django.test import RequestFactory
        rf = RequestFactory().get('/')
        rf.user = self.superuser
        self.assertTrue(admin.site.has_permission(rf))
        rf.user = self.staff_user
        self.assertFalse(admin.site.has_permission(rf))
        rf.user = self.plain_user
        self.assertFalse(admin.site.has_permission(rf))


# ============================================================
# ۲) رندر شدن همهٔ صفحه‌ها
# ============================================================
class PageRenderTests(SuperuserMixin, TestCase):
    def setUp(self):
        self.client = self.login_superuser()

    def test_dashboard_renders_stats(self):
        r = self.client.get(reverse('admin:index'))
        self.assertEqual(r.status_code, 200)
        # باید بخش‌های اصلی داشبورد حضور داشته باشند
        for marker in ('ap-kpi', 'سلامت'):
            self.assertContains(r, marker)

    def test_tool_pages_render(self):
        for name in TOOL_PAGES:
            with self.subTest(page=name):
                r = self.client.get(reverse(name))
                self.assertEqual(r.status_code, 200)
                self.assertContains(r, 'سامانه')

    def test_model_changelists_render(self):
        # یک ردیف واقعی تا همهٔ ستون‌ها (و نه فقط هدرها) رندر شوند
        LogEntry.objects.create(
            user=self.superuser, content_type_id=1, object_id='1',
            object_repr='تست', action_flag=ADDITION, change_message='ایجاد')
        Session.objects.create(session_key='ap' + 'x' * 30,
                               session_data='', expire_date=timezone.now())
        for name in MODEL_PAGES:
            with self.subTest(page=name):
                r = self.client.get(reverse(name))
                self.assertEqual(r.status_code, 200)

    def test_model_add_forms_render(self):
        for name in ('admin:auth_user_add', 'admin:auth_group_add',
                     'admin:users_exitreason_add'):
            with self.subTest(page=name):
                r = self.client.get(reverse(name))
                self.assertEqual(r.status_code, 200)

    def test_login_page_renders(self):
        r = Client().get(reverse('admin:login'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'ورود')

    def test_no_cdn_references_in_admin_pages(self):
        """هیچ صفحه‌ای نباید به CDN خارجی اشاره کند."""
        forbidden = ('cdn.', 'cdnjs', 'unpkg.com', 'jsdelivr',
                     'bootstrapcdn', 'googleapis')
        for name in (['admin:index', 'admin:adminpanel-health',
                      'admin:auth_user_changelist'] + TOOL_PAGES):
            with self.subTest(page=name):
                r = self.client.get(reverse(name))
                body = r.content.decode('utf-8', 'ignore')
                for token in forbidden:
                    self.assertNotIn(token, body,
                                     '%s به %s اشاره دارد' % (name, token))

    def test_sidebar_contains_tool_groups(self):
        r = self.client.get(reverse('admin:index'))
        self.assertContains(r, 'ابزارهای مدیریت')
        self.assertContains(r, 'سلامت و تعمیر سیستم')


# ============================================================
# ۳) ابزارها
# ============================================================
class DiagnosticsTests(SuperuserMixin, TestCase):
    def test_all_checks_run_and_have_shape(self):
        checks = diagnostics.all_checks()
        self.assertTrue(checks)
        for item in checks:
            self.assertIn('status', item)
            self.assertIn('fixable', item)
            self.assertIn('key', item)
            self.assertIn(item['status'], ('ok', 'warn', 'error'))

    def test_fast_checks_are_cheap(self):
        checks = diagnostics.fast_checks()
        self.assertTrue(checks)

    def test_unknown_fix_key_is_safe(self):
        ok, msg = diagnostics.fix_check('does-not-exist')
        self.assertFalse(ok)
        self.assertTrue(msg)

    def test_health_page_lists_checks(self):
        c = self.login_superuser()
        r = c.get(reverse('admin:adminpanel-health'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'بررسی')

    def test_health_fix_all_is_safe(self):
        c = self.login_superuser()
        r = c.post(reverse('admin:adminpanel-health'), {'all': '1'}, follow=True)
        self.assertEqual(r.status_code, 200)


class SqlToolTests(SuperuserMixin, TestCase):
    def setUp(self):
        self.client = self.login_superuser()

    def test_select_runs(self):
        r = self.client.post(reverse('admin:adminpanel-sql'),
                             {'sql': 'SELECT 1 AS one'})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'one')

    def test_write_is_blocked_without_confirmation(self):
        before = Time.objects.count()
        r = self.client.post(reverse('admin:adminpanel-sql'), {
            'sql': 'DELETE FROM users_time',
        })
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'نوشتنی')
        self.assertEqual(Time.objects.count(), before)

    def test_write_requires_exact_confirmation_phrase(self):
        before = Time.objects.count()
        r = self.client.post(reverse('admin:adminpanel-sql'), {
            'sql': 'DELETE FROM users_time',
            'write': 'on',
            'confirm': 'نه',
        })
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Time.objects.count(), before)

    def test_dangerous_statements_are_forbidden(self):
        for sql in ('ATTACH DATABASE x TO y', 'DETACH DATABASE y',
                    'PRAGMA writable_schema = 1',
                    "SELECT load_extension('x')"):
            result = dbtools.run_sql(sql, write=True)
            self.assertFalse(result['ok'], sql)

    def test_run_sql_is_readonly_by_default(self):
        result = dbtools.run_sql('SELECT username FROM auth_user')
        self.assertTrue(result['ok'])
        self.assertEqual(result['affected'], None)

    def test_classify(self):
        self.assertEqual(dbtools.classify('SELECT 1'), 'read')
        self.assertEqual(dbtools.classify('UPDATE users_time SET out=0'),
                         'write')
        self.assertEqual(dbtools.classify('DROP TABLE x'), 'write')
        self.assertEqual(dbtools.classify('ATTACH DATABASE x'), 'forbidden')
        self.assertEqual(
            dbtools.classify('PRAGMA writable_schema = 1'), 'forbidden')
        self.assertEqual(dbtools.classify('   '), 'empty')

    def test_table_stats(self):
        stats = dbtools.table_stats()
        self.assertTrue(stats)


class BackupToolTests(SuperuserMixin, TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='ap-backup-test-')
        self._orig_dir = dbtools.backup_dir
        self._orig_path = dbtools.db_path
        dbtools.backup_dir = lambda: self.tmp  # noqa: E731
        # در تست، دیتابیس روی حافظه است؛ یک فایل SQLite واقعی می‌سازیم
        self.fake_db = os.path.join(self.tmp, 'fake.sqlite3')
        conn = sqlite3.connect(self.fake_db)
        conn.execute('CREATE TABLE IF NOT EXISTS t (id INTEGER)')
        conn.commit()
        conn.close()
        dbtools.db_path = lambda: self.fake_db  # noqa: E731

    def tearDown(self):
        dbtools.backup_dir = self._orig_dir
        dbtools.db_path = self._orig_path
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_create_and_list_backup(self):
        name, msg = dbtools.create_backup('unit-test')
        self.assertTrue(name, msg)
        backups = dbtools.list_backups()
        self.assertTrue(any(b['name'] == name for b in backups))

    def test_path_traversal_is_rejected(self):
        self.assertFalse(dbtools.open_backup('../../etc/passwd'))
        ok, msg = dbtools.delete_backup('../outside.db')
        self.assertFalse(ok)

    def test_view_creates_backup(self):
        c = self.login_superuser()
        r = c.post(reverse('admin:adminpanel-backup'),
                   {'action': 'create', 'label': 'view-test'}, follow=True)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(dbtools.list_backups())

    def test_restore_nonexistent_is_reported(self):
        ok, msg = dbtools.restore_backup('missing-file.db')
        self.assertFalse(ok)

    def test_backup_roundtrip(self):
        name, msg = dbtools.create_backup('round')
        self.assertTrue(name, msg)
        ok, msg = dbtools.restore_backup(name)
        self.assertTrue(ok, msg)

    def test_delete_backup(self):
        name, _ = dbtools.create_backup('del')
        self.assertTrue(name)
        ok, msg = dbtools.delete_backup(name)
        self.assertTrue(ok, msg)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, name)))


class LogToolTests(SuperuserMixin, TestCase):
    def test_tail_returns_list(self):
        from adminpanel import logtools
        rows = logtools.tail(lines=50)
        self.assertIsInstance(rows, list)

    def test_stats_shape(self):
        from adminpanel import logtools
        rows = ['WARNING 2026-01-01 x: hello',
                'ERROR 2026-01-01 x: boom']
        stats = logtools.stats(rows)
        self.assertIn('WARNING', stats)
        self.assertIn('ERROR', stats)

    def test_logs_page(self):
        c = self.login_superuser()
        r = c.get(reverse('admin:adminpanel-logs'))
        self.assertEqual(r.status_code, 200)


class BulkUserToolTests(SuperuserMixin, TestCase):
    def test_get_valid_username(self):
        self.assertEqual(utils.get_valid_username(' ali reza '),
                         'ali_reza')
        self.assertEqual(utils.get_valid_username('a b'), 'a_b')
        self.assertEqual(utils.get_valid_username(''), 'user')
        self.assertEqual(utils.get_valid_username(None), 'user')
        # نویسه‌های غیرمجاز حذف می‌شوند
        self.assertEqual(utils.get_valid_username('bad;name x'),
                         'badname_x')
        self.assertEqual(utils.get_valid_username('a/b\\c'), 'abc')
        self.assertEqual(utils.get_valid_username('  ok.user@x  '),
                         'ok.user@x')
        self.assertEqual(utils.get_valid_username('مستعار'), 'مستعار')
        self.assertLessEqual(len(utils.get_valid_username('x' * 400)), 150)

    def test_random_password_strength(self):
        for _ in range(20):
            pw = utils.random_password(12)
            self.assertEqual(len(pw), 12)
            self.assertTrue(any(c.islower() for c in pw))
            self.assertTrue(any(c.isupper() for c in pw))
            self.assertTrue(any(c.isdigit() for c in pw))

    def test_bulk_create_users(self):
        c = self.login_superuser()
        r = c.post(reverse('admin:adminpanel-users-create'), {
            'lines': 'tool_emp1,ابول,فاضل,,user,\ntool_emp2,سارا,محمدی,,user,',
            'default_role': 'user',
            'default_password': '',
        }, follow=True)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(
            User.objects.filter(username='tool_emp1').exists())
        created = User.objects.get(username='tool_emp2')
        # رمز خالی یعنی رمز تصادفی ساخته شده باشد
        self.assertTrue(created.has_usable_password())
        User.objects.filter(
            username__in=['tool_emp1', 'tool_emp2']).delete()

    def test_bulk_action_deactivate_and_reset(self):
        c = self.login_superuser()
        u = User.objects.create_user(username='tool_bulk',
                                     password='X!abc12345')
        r = c.post(reverse('admin:adminpanel-users-bulk'), {
            'action': 'deactivate', 'ids': str(u.pk),
        }, follow=True)
        self.assertEqual(r.status_code, 200)
        u.refresh_from_db()
        self.assertFalse(u.is_active)

        r = c.post(reverse('admin:adminpanel-users-bulk'), {
            'action': 'reset_password', 'ids': str(u.pk),
        })
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'رمزهای جدید')
        u.refresh_from_db()
        self.assertFalse(u.check_password('X!abc12345'))
        u.delete()

    def test_bulk_action_never_touches_superuser(self):
        c = self.login_superuser()
        r = c.post(reverse('admin:adminpanel-users-bulk'), {
            'action': 'delete', 'ids': str(self.superuser.pk),
        }, follow=True)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(User.objects.filter(pk=self.superuser.pk).exists())

    def test_bulk_create_tool_page(self):
        c = self.login_superuser()
        r = c.get(reverse('admin:adminpanel-users-create'))
        self.assertEqual(r.status_code, 200)


class AttendanceToolTests(SuperuserMixin, TestCase):
    def test_rebuild_present(self):
        c = self.login_superuser()
        r = c.post(reverse('admin:adminpanel-attendance'), {
            'action': 'present_mismatch',
        }, follow=True)
        self.assertEqual(r.status_code, 200)

    def test_close_open_breaks(self):
        from users.models import Break
        # استراحتِ باز باید مربوط به روز گذشته باشد تا «بسته» شود
        past = timezone_now() - dt.timedelta(days=1, hours=3)
        Break.objects.create(user=self.superuser,
                             date=past.date(), break_out=past)
        c = self.login_superuser()
        r = c.post(reverse('admin:adminpanel-attendance'), {
            'action': 'open_breaks', 'minutes': '30',
        }, follow=True)
        self.assertEqual(r.status_code, 200)
        br = Break.objects.filter(user=self.superuser).first()
        self.assertIsNotNone(br)
        self.assertIsNotNone(br.break_in)
        Break.objects.all().delete()

    def test_sessions_and_snapshots_pages(self):
        c = self.login_superuser()
        for name in ('admin:adminpanel-sessions',
                     'admin:adminpanel-snapshots',
                     'admin:adminpanel-rename'):
            r = c.get(reverse(name))
            self.assertEqual(r.status_code, 200, name)


def timezone_now():
    from django.utils import timezone
    return timezone.now()


# ============================================================
# ۴) ثبت مدل‌ها و عملیات سفارشی
# ============================================================
class ModelRegistrationTests(SuperuserMixin, TestCase):
    def test_extra_models_registered(self):
        for model in (User, ExitReason, Time, LogEntry, Session):
            self.assertIn(model, admin.site._registry, model)

    def test_custom_model_admins_are_used(self):
        from adminpanel.admin import GroupAdmin, LogEntryAdmin, SessionAdmin
        from django.contrib.auth.models import Group
        from users.admin import AppUserAdmin, TimeAdmin
        self.assertIsInstance(admin.site._registry[Group], GroupAdmin)
        self.assertIsInstance(admin.site._registry[LogEntry], LogEntryAdmin)
        self.assertIsInstance(admin.site._registry[Session], SessionAdmin)
        self.assertIsInstance(admin.site._registry[User], AppUserAdmin)
        self.assertIsInstance(admin.site._registry[Time], TimeAdmin)

    def test_user_admin_is_fast(self):
        from users.admin import AppUserAdmin
        ma = AppUserAdmin(User, admin.site)
        self.assertFalse(ma.show_full_result_count)
        self.assertEqual(ma.list_select_related, ('profile',))

    def test_logentry_is_readonly(self):
        from adminpanel.admin import LogEntryAdmin
        ma = LogEntryAdmin(LogEntry, admin.site)
        self.assertFalse(ma.has_add_permission(None))
        self.assertFalse(ma.has_change_permission(None))


class CustomActionTests(SuperuserMixin, TestCase):
    """عملیات گروهی سفارشی روی فهرست‌ها."""

    def setUp(self):
        self.client = self.login_superuser()
        self.reason = ExitReason.objects.create(title='تست دلیل')
        self.record = Time.objects.create(
            user=self.superuser,
            date=dt.date.today(),
            out=True,
        )

    def tearDown(self):
        Time.objects.filter(pk=self.record.pk).delete()
        self.reason.delete()

    def test_assign_reason_intermediate_page(self):
        r = self.client.post(reverse('admin:users_time_changelist'), {
            'action': 'act_assign_reason',
            'index': '0',
            '_selected_action': [str(self.record.pk)],
        })
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'apply')
        self.assertContains(r, self.reason.title)

    def test_assign_reason_applies(self):
        self.client.post(reverse('admin:users_time_changelist'), {
            'action': 'act_assign_reason',
            'index': '0',
            'apply': '1',
            'reason': str(self.reason.pk),
            '_selected_action': [str(self.record.pk)],
        }, follow=True)
        self.record.refresh_from_db()
        self.assertEqual(self.record.exit_reason_id, self.reason.pk)

    def test_assign_reason_clears(self):
        Time.objects.filter(pk=self.record.pk).update(
            exit_reason=self.reason)
        self.client.post(reverse('admin:users_time_changelist'), {
            'action': 'act_assign_reason',
            'index': '0',
            'apply': '1',
            'reason': '',
            '_selected_action': [str(self.record.pk)],
        }, follow=True)
        self.record.refresh_from_db()
        self.assertIsNone(self.record.exit_reason_id)

    def test_mark_in_out(self):
        self.client.post(reverse('admin:users_time_changelist'), {
            'action': 'act_mark_in', 'index': '0',
            '_selected_action': [str(self.record.pk)],
        }, follow=True)
        self.record.refresh_from_db()
        self.assertFalse(self.record.out)
        self.client.post(reverse('admin:users_time_changelist'), {
            'action': 'act_mark_out', 'index': '0',
            '_selected_action': [str(self.record.pk)],
        }, follow=True)
        self.record.refresh_from_db()
        self.assertTrue(self.record.out)

    def test_export_returns_xlsx(self):
        r = self.client.post(reverse('admin:users_time_changelist'), {
            'action': 'act_export', 'index': '0',
            '_selected_action': [str(self.record.pk)],
        })
        self.assertEqual(r.status_code, 200)
        self.assertIn('spreadsheet', r['Content-Type'])
        self.assertTrue(r.content.startswith(b'PK'))

    def test_reset_passwords_shows_new_passwords(self):
        victim = User.objects.create_user(username='ap_victim',
                                          password='OldPass!123')
        r = self.client.post(reverse('admin:auth_user_changelist'), {
            'action': 'act_reset_passwords', 'index': '0',
            '_selected_action': [str(victim.pk)],
        })
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'رمزهای جدید')
        self.assertContains(r, 'ap-password')
        victim.refresh_from_db()
        self.assertFalse(victim.check_password('OldPass!123'))
        victim.delete()

    def test_sync_profiles_creates_profile(self):
        u = User.objects.create_user(username='ap_noprof',
                                     password='X!abc12345')
        self.assertFalse(UserProfile.objects.filter(user=u).exists())
        self.client.post(reverse('admin:auth_user_changelist'), {
            'action': 'act_sync_profiles', 'index': '0',
            '_selected_action': [str(u.pk)],
        }, follow=True)
        self.assertTrue(UserProfile.objects.filter(user=u).exists())
        u.delete()

    def test_force_logout(self):
        from django.contrib.sessions.backends.db import SessionStore
        s = SessionStore()
        s['_auth_user_id'] = str(self.plain_user.pk)
        s.create()
        self.assertEqual(Session.objects.filter(
            session_key=s.session_key).count(), 1)
        self.client.post(reverse('admin:auth_user_changelist'), {
            'action': 'act_force_logout', 'index': '0',
            '_selected_action': [str(self.plain_user.pk)],
        }, follow=True)
        self.assertEqual(Session.objects.filter(
            session_key=s.session_key).count(), 0)

    def test_exit_reason_activate_action(self):
        r2 = ExitReason.objects.create(title='غیرفعال تست', is_active=False)
        self.client.post(reverse('admin:users_exitreason_changelist'), {
            'action': 'act_activate', 'index': '0',
            '_selected_action': [str(r2.pk)],
        }, follow=True)
        r2.refresh_from_db()
        self.assertTrue(r2.is_active)
        r2.delete()


class DashboardTests(SuperuserMixin, TestCase):
    def test_dashboard_kpis(self):
        from adminpanel.dashboard import build_dashboard
        c = self.login_superuser()
        data = build_dashboard(c.get('/').wsgi_request)
        self.assertIn('kpis', data)

    def test_dashboard_never_raises_on_empty_db(self):
        from adminpanel.dashboard import build_dashboard
        c = self.login_superuser()
        data = build_dashboard(c.get('/').wsgi_request)
        self.assertIsInstance(data, dict)
