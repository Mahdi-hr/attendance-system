"""تست‌های صفحهٔ گزارش حضور شخصی (`/view_my_attendance`)."""

import datetime as dt

import jdatetime
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from users.models import Time


def aware(day, hour):
    return timezone.make_aware(
        dt.datetime.combine(day, dt.time(hour, 0)),
        timezone.get_current_timezone(),
    )


def jalali(value):
    j = jdatetime.date.fromgregorian(date=value)
    return '%04d/%02d/%02d' % (j.year, j.month, j.day)


class MyAttendanceReportTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            'emp', password='x', first_name='کارمند',
        )
        self.client.force_login(self.user)
        self.url = reverse('view-my-attendance-employee-login')
        self.today = timezone.localdate()
        self.days = [
            self.today - dt.timedelta(days=offset)
            for offset in range(10)
        ]
        for day in self.days:
            Time.objects.create(
                user=self.user, date=day, out=False,
                time=aware(day, 9),
            )
            Time.objects.create(
                user=self.user, date=day, out=True,
                time=aware(day, 17),
            )

    def test_without_range_does_not_show_all_history(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context['total_text'])
        self.assertEqual(response.context['rows'], [])
        self.assertContains(response, 'بازه تاریخ را انتخاب کنید')
        self.assertNotContains(response, 'خلاصه هفتگی')

    def test_date_filter_limits_rows(self):
        start = self.days[2]
        end = self.days[0]
        response = self.client.get(self.url, {
            'date_from': jalali(start),
            'date_to': jalali(end),
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [row['date'] for row in response.context['rows']],
            list(reversed(self.days[:3])),
        )
        self.assertEqual(response.context['days_present'], 3)
        self.assertNotContains(response, 'خلاصه هفتگی')

    def test_daily_chart_shows_only_last_seven_present_days(self):
        response = self.client.get(self.url, {
            'date_from': jalali(self.days[-1]),
            'date_to': jalali(self.days[0]),
        })
        self.assertEqual(response.status_code, 200)
        chart_rows = response.context['chart_rows']
        self.assertEqual(len(chart_rows), 7)
        self.assertEqual(
            [row['date'] for row in chart_rows],
            list(reversed(self.days[:7])),
        )

    def test_invalid_reversed_range_shows_no_data(self):
        response = self.client.get(self.url, {
            'date_from': jalali(self.days[0]),
            'date_to': jalali(self.days[5]),
        })
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context['total_text'])
        self.assertEqual(response.context['rows'], [])
        self.assertContains(response, 'بازه تاریخ را انتخاب کنید')
