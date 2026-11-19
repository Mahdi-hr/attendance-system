"""تست‌های اپ گزارش کارکرد ماهیانه پروژه‌ها."""

import datetime as dt
from decimal import Decimal
from io import BytesIO

import jdatetime
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from openpyxl import load_workbook

from users.models import Time
from . import services
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
        self.assertEqual(len(data['rows']), 3)
        self.assertEqual(data['rows'][0]['title'], 'zabbix')
        self.assertEqual(data['rows'][0]['values'][0], Decimal('5.00'))
        self.assertEqual(data['grand_total'], Decimal('8.00'))
        self.assertEqual(data['total_by_day'][0], Decimal('8.00'))


class ViewTests(WorklogTestBase):
    def test_employee_can_save_and_see_matrix(self):
        self.client.force_login(self.emp)
        response = self.client.post('/worklog/', self._data(**{
            self.kp1: '5', self.kp2: '2', 'other_1': '1', 'other_title': 'پشتیبانی',
        }), follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(WorkLog.objects.filter(user=self.emp).count(), 3)

    def test_employee_cannot_open_admin_pages(self):
        self.client.force_login(self.emp)
        response = self.client.get('/worklog/admin/')
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
