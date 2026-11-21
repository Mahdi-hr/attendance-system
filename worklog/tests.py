"""تست‌های اپ گزارش کارکرد ماهیانه پروژه‌ها."""

import datetime as dt
from decimal import Decimal
from io import BytesIO

import jdatetime
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from openpyxl import load_workbook

from users.models import Time, UserProfile
from errands.models import Errand
from . import services
from .admin_excel import build_admin_projects_xlsx
from .excel import build_worklog_xlsx
from .models import Project, WorkLog, WorkLogMonth

User = get_user_model()


def day_in_current_month(day=1):
    j = jdatetime.date.fromgregorian(date=timezone.localdate())
    return jdatetime.date(j.year, j.month, day).togregorian()


class WorklogTestBase(TestCase):
    def setUp(self):
        self.emp = User.objects.create_user(
            'emp', password='Secret12345',
            first_name='کارمند', last_name='تست',
        )
        self.admin = User.objects.create_superuser(
            'boss', 'boss@example.com', 'Secret12345',
        )
        self.p1, _ = Project.objects.get_or_create(
            name='zabbix', defaults={'sort_order': 1},
        )
        self.p2, _ = Project.objects.get_or_create(
            name='attendance', defaults={'sort_order': 2},
        )
        self.year, self.month = services.current_month()
        self.day = day_in_current_month(1)
        self.kp1 = 'p%d_1' % self.p1.pk
        self.kp2 = 'p%d_1' % self.p2.pk
        self.kp1d2 = 'p%d_2' % self.p1.pk
        self._add_attendance(self.emp, self.day, 9, 17)  # 8 ساعت

    def _add_attendance(self, user, date, start_hour, end_hour):
        tz = timezone.get_current_timezone()
        Time.objects.create(
            user=user, date=date, out=False,
            time=timezone.make_aware(
                dt.datetime.combine(date, dt.time(start_hour, 0)), tz,
            ),
        )
        Time.objects.create(
            user=user, date=date, out=True,
            time=timezone.make_aware(
                dt.datetime.combine(date, dt.time(end_hour, 0)), tz,
            ),
        )

    def _data(self, **values):
        data = {
            'year': self.year,
            'month': self.month,
            'other_title': '',
        }
        data.update(values)
        return data


class ServiceTests(WorklogTestBase):
    def test_month_bounds_mehr(self):
        start, end, days = services.month_bounds(1405, 7)
        self.assertEqual(days, 30)

    def test_attendance_hours(self):
        attendance = services.attendance_map(self.emp, self.year, self.month)
        self.assertIn(self.day, attendance)
        self.assertEqual(
            services.worked_hours_for(attendance, self.day),
            Decimal('8.00'),
        )

    def test_parse_hours_persian_digits(self):
        value, error = services.parse_hours('۲٫۵')
        self.assertIsNone(error)
        self.assertEqual(value, Decimal('2.50'))

    def test_save_allocations_creates_rows(self):
        ok, errors = services.save_allocations(
            self.emp, self.year, self.month, [self.p1, self.p2],
            self._data(**{self.kp1: '5', self.kp2: '2', 'other_1': '1', 'other_title': 'پشتیبانی'}),
        )
        self.assertTrue(ok, errors)
        logs = WorkLog.objects.filter(user=self.emp, date=self.day)
        self.assertEqual(logs.count(), 3)
        self.assertEqual(logs.get(project=self.p1).hours, Decimal('5.00'))
        self.assertEqual(logs.get(project=self.p2).hours, Decimal('2.00'))
        self.assertEqual(logs.get(project__isnull=True).other_title, 'پشتیبانی')

    def test_over_limit_is_rejected(self):
        ok, errors = services.save_allocations(
            self.emp, self.year, self.month, [self.p1],
            self._data(**{self.kp1: '9'}),
        )
        self.assertFalse(ok)
        self.assertTrue(any('بیشتر است' in e for e in errors))
        self.assertEqual(WorkLog.objects.count(), 0)

    def test_day_without_attendance_is_rejected(self):
        ok, errors = services.save_allocations(
            self.emp, self.year, self.month, [self.p1],
            self._data(**{self.kp1d2: '1'}),
        )
        self.assertFalse(ok)
        self.assertTrue(any('ثبت نشده' in e for e in errors))

    def test_other_requires_title(self):
        ok, errors = services.save_allocations(
            self.emp, self.year, self.month, [self.p1],
            self._data(**{self.kp1: '5', 'other_1': '1'}),
        )
        self.assertFalse(ok)
        self.assertTrue(any('عنوان' in e for e in errors))

    def test_report_data_and_totals(self):
        services.save_allocations(
            self.emp, self.year, self.month, [self.p1, self.p2],
            self._data(**{self.kp1: '5', self.kp2: '2', 'other_1': '1', 'other_title': 'پشتیبانی'}),
        )
        data = services.report_data(self.emp, self.year, self.month)
        self.assertEqual(len(data['rows']), 4)
        self.assertEqual(data['rows'][0]['title'], 'zabbix')
        self.assertTrue(data['rows'][2]['auto'])
        self.assertEqual(data['rows'][2]['title'], 'مأموریت شرکتی (تأییدشده)')
        self.assertEqual(data['rows'][0]['values'][0], Decimal('5.00'))
        self.assertEqual(data['grand_total'], Decimal('8.00'))
        self.assertEqual(data['total_by_day'][0], Decimal('8.00'))


class ErrandWorklogTests(WorklogTestBase):
    """مأموریت تأییدشده خودکار در گزارش می‌آید و کاربر دخالتی ندارد."""

    def _add_errand(self, status, work_mode=Errand.MODE_REMOTE,
                    start_hour=17, end_hour=19, destination=''):
        tz = timezone.get_current_timezone()
        return Errand.objects.create(
            user=self.emp,
            start_time=timezone.make_aware(
                dt.datetime.combine(self.day, dt.time(start_hour, 0)), tz,
            ),
            end_time=timezone.make_aware(
                dt.datetime.combine(self.day, dt.time(end_hour, 0)), tz,
            ),
            reason='مأموریت تست',
            status=status,
            work_mode=work_mode,
            destination=destination,
        )

    def test_approved_errand_is_automatic(self):
        self._add_errand(
            Errand.STATUS_APPROVED, Errand.MODE_COMPANY,
            destination='بانک ملت',
        )

        matrix = services.month_matrix(
            self.emp, self.year, self.month, [self.p1],
        )
        day = next(d for d in matrix['days'] if d['day'] == 1)
        self.assertEqual(day['worked'], Decimal('10.00'))
        self.assertEqual(day['allocatable'], Decimal('8.00'))
        self.assertEqual(day['errand'], Decimal('2.00'))
        self.assertEqual(matrix['errand_total'], Decimal('2.00'))
        self.assertEqual(matrix['errand_label'], 'مأموریت شرکتی — بانک ملت')
        self.assertEqual(matrix['errand_destinations'], ['بانک ملت'])

        # کاربر فقط ۸ ساعت حضور فیزیکی را می‌تواند بین پروژه‌ها تقسیم کند.
        ok, errors = services.save_allocations(
            self.emp, self.year, self.month, [self.p1],
            self._data(**{self.kp1: '8'}),
        )
        self.assertTrue(ok, errors)
        totals = services.month_totals(self.emp, self.year, self.month)
        self.assertEqual(totals['worked'], Decimal('10.00'))
        self.assertEqual(totals['allocated'], Decimal('10.00'))
        self.assertEqual(totals['remaining'], Decimal('0.00'))

        # ۹ ساعت پروژه بیش از حضور فیزیکی است و باید رد شود.
        ok, errors = services.save_allocations(
            self.emp, self.year, self.month, [self.p1],
            self._data(**{self.kp1: '9'}),
        )
        self.assertFalse(ok)
        self.assertTrue(any('قابل تخصیص' in e for e in errors))

        data = services.report_data(self.emp, self.year, self.month)
        auto = next(row for row in data['rows'] if row.get('auto'))
        self.assertEqual(auto['title'], 'مأموریت شرکتی — بانک ملت')
        self.assertEqual(auto['values'][0], Decimal('2.00'))
        self.assertEqual(data['grand_total'], Decimal('10.00'))

    def test_remote_errand_is_allocatable(self):
        self._add_errand(Errand.STATUS_APPROVED, Errand.MODE_REMOTE)
        matrix = services.month_matrix(
            self.emp, self.year, self.month, [self.p1],
        )
        day = next(d for d in matrix['days'] if d['day'] == 1)
        self.assertEqual(day['worked'], Decimal('10.00'))
        self.assertEqual(day['allocatable'], Decimal('10.00'))
        self.assertEqual(day['errand'], Decimal('0.00'))
        self.assertEqual(matrix['errand_total'], Decimal('0.00'))

        ok, errors = services.save_allocations(
            self.emp, self.year, self.month, [self.p1],
            self._data(**{self.kp1: '10'}),
        )
        self.assertTrue(ok, errors)
        totals = services.month_totals(self.emp, self.year, self.month)
        self.assertEqual(totals['worked'], Decimal('10.00'))
        self.assertEqual(totals['allocated'], Decimal('10.00'))
        self.assertEqual(totals['remaining'], Decimal('0.00'))

        data = services.report_data(self.emp, self.year, self.month)
        auto = next(row for row in data['rows'] if row.get('auto'))
        self.assertEqual(auto['total'], Decimal('0.00'))

    def test_pending_errand_is_ignored(self):
        self._add_errand(Errand.STATUS_PENDING)
        matrix = services.month_matrix(
            self.emp, self.year, self.month, [self.p1],
        )
        day = next(d for d in matrix['days'] if d['day'] == 1)
        self.assertEqual(day['errand'], Decimal('0.00'))
        self.assertEqual(day['worked'], Decimal('8.00'))

        data = services.report_data(self.emp, self.year, self.month)
        auto = next(row for row in data['rows'] if row.get('auto'))
        self.assertEqual(auto['total'], Decimal('0.00'))

    def test_excel_contains_automatic_errand_row(self):
        self._add_errand(
            Errand.STATUS_APPROVED, Errand.MODE_COMPANY,
            destination='بانک ملت',
        )
        services.save_allocations(
            self.emp, self.year, self.month, [self.p1],
            self._data(**{self.kp1: '8'}),
        )
        content, _filename = build_worklog_xlsx(
            self.emp, self.year, self.month,
        )
        wb = load_workbook(BytesIO(content))
        ws = wb.active

        auto_row = None
        total_row = None
        for row in range(6, ws.max_row + 1):
            if ws.cell(row=row, column=2).value == 'مأموریت شرکتی — بانک ملت':
                auto_row = row
            if ws.cell(row=row, column=1).value == 'جمع':
                total_row = row
        self.assertIsNotNone(auto_row)
        self.assertIsNotNone(total_row)
        self.assertEqual(ws.cell(row=auto_row, column=3).value, 2.0)
        self.assertEqual(ws.cell(row=total_row, column=34).value, 10.0)

    def test_excel_splits_company_errands_by_destination(self):
        self._add_errand(
            Errand.STATUS_APPROVED, Errand.MODE_COMPANY,
            start_hour=17, end_hour=19, destination='بانک ملت',
        )
        self._add_errand(
            Errand.STATUS_APPROVED, Errand.MODE_COMPANY,
            start_hour=19, end_hour=20, destination='دادگاه',
        )
        services.save_allocations(
            self.emp, self.year, self.month, [self.p1],
            self._data(**{self.kp1: '8'}),
        )
        content, _filename = build_worklog_xlsx(
            self.emp, self.year, self.month,
        )
        wb = load_workbook(BytesIO(content))
        ws = wb.active

        bank_row = court_row = total_row = None
        for row in range(6, ws.max_row + 1):
            title = ws.cell(row=row, column=2).value
            if title == 'مأموریت شرکتی — بانک ملت':
                bank_row = row
            if title == 'مأموریت شرکتی — دادگاه':
                court_row = row
            if ws.cell(row=row, column=1).value == 'جمع':
                total_row = row
        self.assertIsNotNone(bank_row)
        self.assertIsNotNone(court_row)
        self.assertIsNotNone(total_row)
        self.assertEqual(ws.cell(row=bank_row, column=3).value, 2.0)
        self.assertEqual(ws.cell(row=court_row, column=3).value, 1.0)
        self.assertEqual(ws.cell(row=total_row, column=34).value, 11.0)


class AdminProjectExportTests(WorklogTestBase):
    """اکسل جمعی پروژه‌ها برای ادمین."""

    def setUp(self):
        super().setUp()
        self.emp2 = User.objects.create_user(
            'emp2', password='Secret12345',
            first_name='کارمند', last_name='دوم',
        )
        WorkLog.objects.create(
            user=self.emp, date=self.day, project=self.p1,
            hours=Decimal('5.00'),
        )
        WorkLog.objects.create(
            user=self.emp, date=self.day, project=self.p2,
            hours=Decimal('2.00'),
        )
        WorkLog.objects.create(
            user=self.emp, date=self.day, project=None,
            other_title='پشتیبانی', hours=Decimal('1.00'),
        )
        WorkLog.objects.create(
            user=self.emp2, date=self.day, project=self.p1,
            hours=Decimal('3.00'),
        )
        WorkLog.objects.create(
            user=self.emp2, date=self.day, project=self.p2,
            hours=Decimal('1.00'),
        )

    def test_summary_totals_per_project_and_user(self):
        data = services.admin_project_summary(self.year, self.month)
        self.assertEqual(len(data['users']), 2)
        rows = {row['name']: row for row in data['projects']}
        self.assertEqual(rows['zabbix']['total'], Decimal('8.00'))
        self.assertEqual(rows['attendance']['total'], Decimal('3.00'))
        self.assertEqual(rows['سایر']['total'], Decimal('1.00'))
        self.assertEqual(data['grand_total'], Decimal('12.00'))
        self.assertEqual(data['total_by_user'][self.emp.pk], Decimal('8.00'))
        self.assertEqual(data['total_by_user'][self.emp2.pk], Decimal('4.00'))

    def test_workbook_has_month_and_year_sheets(self):
        content, filename = build_admin_projects_xlsx(
            self.year, self.month,
        )
        self.assertIn('worklog-projects', filename)
        wb = load_workbook(BytesIO(content))
        self.assertEqual(
            wb.sheetnames, ['جمع پروژه‌ها', 'روند سالانه'],
        )

        ws = wb['جمع پروژه‌ها']
        headers = {}
        for col in range(1, ws.max_column + 1):
            value = ws.cell(row=4, column=col).value
            if value:
                headers[value] = col
        self.assertIn('پروژه', headers)
        self.assertIn('جمع کل', headers)

        zabbix_row = None
        for row in range(5, ws.max_row + 1):
            if ws.cell(row=row, column=2).value == 'zabbix':
                zabbix_row = row
                break
        self.assertIsNotNone(zabbix_row)
        self.assertEqual(
            ws.cell(row=zabbix_row, column=headers['جمع کل']).value,
            8.0,
        )

        year_ws = wb['روند سالانه']
        zabbix_year_row = None
        for row in range(5, year_ws.max_row + 1):
            if year_ws.cell(row=row, column=2).value == 'zabbix':
                zabbix_year_row = row
                break
        self.assertIsNotNone(zabbix_year_row)
        self.assertEqual(
            year_ws.cell(
                row=zabbix_year_row, column=2 + self.month,
            ).value,
            8.0,
        )

    def test_admin_export_view_and_permissions(self):
        self.client.force_login(self.admin)
        response = self.client.get(
            '/worklog/admin/projects-export/?year=%d&month=%d'
            % (self.year, self.month),
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn('spreadsheetml', response['Content-Type'])

        self.client.force_login(self.emp)
        response = self.client.get(
            '/worklog/admin/projects-export/?year=%d&month=%d'
            % (self.year, self.month),
        )
        self.assertEqual(response.status_code, 302)


class ViewTests(WorklogTestBase):
    def test_employee_can_save_and_see_matrix(self):
        self.client.force_login(self.emp)
        response = self.client.post('/worklog/', self._data(**{
            self.kp1: '5', self.kp2: '2', 'other_1': '1', 'other_title': 'پشتیبانی',
        }), follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(WorkLog.objects.filter(user=self.emp).count(), 3)

    def test_mobile_view_uses_day_cards(self):
        self.client.force_login(self.emp)
        self.client.post('/worklog/', self._data(**{self.kp1: '5'}), follow=True)

        response = self.client.get(
            '/worklog/?year=%d&month=%d&view=mobile'
            % (self.year, self.month),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'wl-m-card')
        self.assertNotContains(response, '<table class="wl-table')
        self.assertContains(response, 'name="%s"' % self.kp1)
        # فقط پروژه‌های دارای ساعت نمایش داده می‌شوند، نه همهٔ پروژه‌ها.
        self.assertNotContains(response, 'name="%s"' % 'p%d_1' % self.p2.pk)
        self.assertContains(response, 'class="wl-m-add"')

    def test_mobile_ua_gets_mobile_view(self):
        self.client.force_login(self.emp)
        response = self.client.get(
            '/worklog/?year=%d&month=%d' % (self.year, self.month),
            HTTP_USER_AGENT='Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)',
        )
        self.assertContains(response, 'wl-m-card')

    def test_employee_cannot_open_admin_pages(self):
        self.client.force_login(self.emp)
        response = self.client.get('/worklog/admin/')
        self.assertEqual(response.status_code, 302)

    def test_admin_mode_redirects_employee_worklog_to_admin_list(self):
        self.client.force_login(self.admin)
        response = self.client.get(
            '/worklog/?year=%d&month=%d' % (self.year, self.month),
        )
        self.assertRedirects(
            response,
            '/worklog/admin/?year=%d&month=%d' % (self.year, self.month),
        )

    def test_both_user_in_admin_mode_redirects_to_admin_list(self):
        both = User.objects.create_user('both', password='Secret12345')
        UserProfile.objects.update_or_create(
            user=both, defaults={'role': UserProfile.ROLE_BOTH},
        )
        self.client.force_login(both)

        # در حالت کاربر، صفحهٔ کارکرد شخصی مجاز است.
        response = self.client.get('/worklog/')
        self.assertEqual(response.status_code, 200)

        session = self.client.session
        session['admin_mode'] = True
        session.save()
        response = self.client.get(
            '/worklog/?year=%d&month=%d' % (self.year, self.month),
        )
        self.assertRedirects(
            response,
            '/worklog/admin/?year=%d&month=%d' % (self.year, self.month),
        )

    def test_admin_mode_blocks_personal_export_and_submit(self):
        self.client.force_login(self.admin)
        response = self.client.get('/worklog/export/')
        self.assertEqual(response.status_code, 302)
        response = self.client.post('/worklog/submit/', {
            'year': self.year, 'month': self.month,
        })
        self.assertEqual(response.status_code, 302)

    def test_submit_locks_month_and_prevents_edits(self):
        self.client.force_login(self.emp)
        self.client.post('/worklog/', self._data(**{self.kp1: '5'}), follow=True)
        self.client.post('/worklog/submit/', self._data(**{self.kp1: '5'}), follow=True)

        state = WorkLogMonth.objects.get(
            user=self.emp, year=self.year, month=self.month,
        )
        self.assertTrue(state.is_submitted)

        self.client.post('/worklog/', self._data(**{self.kp1: '8'}), follow=True)
        log = WorkLog.objects.get(user=self.emp, date=self.day, project=self.p1)
        self.assertEqual(log.hours, Decimal('5.00'))

    def test_reopen_month(self):
        self.client.force_login(self.emp)
        self.client.post('/worklog/', self._data(**{self.kp1: '5'}), follow=True)
        self.client.post('/worklog/submit/', self._data(**{self.kp1: '5'}), follow=True)
        self.client.post('/worklog/reopen/', self._data(), follow=True)
        state = WorkLogMonth.objects.get(
            user=self.emp, year=self.year, month=self.month,
        )
        self.assertFalse(state.is_submitted)

    def test_export_returns_valid_xlsx(self):
        self.client.force_login(self.emp)
        self.client.post('/worklog/', self._data(**{
            self.kp1: '5', self.kp2: '2', 'other_1': '1', 'other_title': 'پشتیبانی',
        }), follow=True)

        response = self.client.get(
            '/worklog/export/?year=%d&month=%d' % (self.year, self.month),
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn('spreadsheetml', response['Content-Type'])

        wb = load_workbook(BytesIO(response.content))
        ws = wb.active
        self.assertEqual(ws.title, 'گزارش کارکرد')
        self.assertEqual(ws['A2'].value, 'گزارش کارکرد ماهیانه')
        self.assertIn('کارمند', ws['B3'].value)

        # ردیف اول داده از ردیف ۶ شروع می‌شود.
        self.assertEqual(ws['A6'].value, 1)
        self.assertEqual(ws['B6'].value, 'zabbix')
        self.assertEqual(ws['C6'].value, 5.0)
        self.assertEqual(ws['AH6'].value, 5.0)

    def test_admin_list_and_read_only_detail(self):
        self.client.force_login(self.admin)
        response = self.client.get('/worklog/admin/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'کارمند تست')

        response = self.client.get(
            '/worklog/admin/%d/?year=%d&month=%d'
            % (self.emp.pk, self.year, self.month),
        )
        self.assertEqual(response.status_code, 200)
        # در نمای مدیر هیچ input ثبت ساعتی نباید باشد.
        self.assertNotContains(response, 'name="p%d_1"' % self.p1.pk)

        mobile = self.client.get(
            '/worklog/admin/%d/?year=%d&month=%d&view=mobile'
            % (self.emp.pk, self.year, self.month),
        )
        self.assertEqual(mobile.status_code, 200)
        self.assertContains(mobile, 'wl-m-card')
        self.assertNotContains(mobile, 'name="p%d_1"' % self.p1.pk)

    def test_project_crud_admin_only(self):
        self.client.force_login(self.emp)
        response = self.client.get('/worklog/projects/')
        self.assertEqual(response.status_code, 302)

        self.client.force_login(self.admin)
        response = self.client.get('/worklog/projects/')
        self.assertEqual(response.status_code, 200)

        response = self.client.post('/worklog/projects/add/', {
            'name': 'new-project',
            'code': 'np',
            'description': 'پروژه تست',
            'sort_order': 9,
            'is_active': 'on',
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(Project.objects.filter(name='new-project').exists())

    def test_toggle_project(self):
        self.client.force_login(self.admin)
        self.client.post('/worklog/projects/%d/toggle/' % self.p1.pk, follow=True)
        self.p1.refresh_from_db()
        self.assertFalse(self.p1.is_active)
