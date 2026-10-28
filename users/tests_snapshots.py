"""تست‌های بازبینی عکس‌های لحظه.

سناریوی اصلی باگ: رکورد «جدیدترین» عکس در دیتابیس هست ولی فایلش روی سرور
نیست (مثلاً بعد از بازیابی دیتابیس). قبلاً اولین کارت صفحه عکس شکسته بود؛
حالا باید جدیدترین عکسِ **موجود** نمایش داده شود و تعداد رکوردهای بی‌فایل
هشدار داده شود.
"""

import datetime as dt
import os
import shutil
import tempfile
import time

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from users.models import AttendanceSnapshot
from users.snapshots import snapshots_root


User = get_user_model()


class SnapshotReviewTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='snap-test-')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.override = override_settings(BASE_DIR=self.tmp)
        self.override.enable()
        self.addCleanup(self.override.disable)

        self.admin = User.objects.create_superuser(
            'snapadmin', 'snap@example.com', 'Secret12345',
        )
        self.emp = User.objects.create_user('emp', password='Secret12345')
        self.client.force_login(self.admin)

    # ---------- کمک‌کننده‌ها ----------
    def _snap(self, photo, minutes_ago=0, event='in'):
        snap = AttendanceSnapshot.objects.create(
            user=self.emp, event=event, photo=photo,
        )
        created = timezone.now() - dt.timedelta(minutes=minutes_ago)
        # created_at با auto_now_add ساخته می‌شود؛ برای تست با update عوضش می‌کنیم.
        AttendanceSnapshot.objects.filter(pk=snap.pk).update(created_at=created)
        snap.refresh_from_db()
        return snap

    def _file(self, photo, content=b'\xff\xd8\xff\xd9'):
        path = os.path.join(snapshots_root(), photo)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as fh:
            fh.write(content)
        return path

    # ---------- تست‌ها ----------
    def test_view_shows_newest_existing_and_skips_missing(self):
        old = self._snap('emp/old.jpg', minutes_ago=120)
        mid = self._snap('emp/mid.jpg', minutes_ago=60)
        self._snap('emp/newest-missing.jpg', minutes_ago=0)  # فایل ندارد

        self._file('emp/old.jpg')
        self._file('emp/mid.jpg')

        response = self.client.get('/manage/snapshots/?username=emp')
        self.assertEqual(response.status_code, 200)

        shown = response.context['snapshots']
        self.assertEqual([s.id for s in shown], [mid.id, old.id])
        self.assertEqual(response.context['total_count'], 3)
        self.assertEqual(response.context['missing_count'], 1)

        html = response.content.decode()
        self.assertIn('/manage/snapshots/image/%d/' % mid.id, html)
        # کارت جدیدترین رکورد (بی‌فایل) نباید در گرید باشد.
        self.assertNotIn('emp/newest-missing.jpg', html)

    def test_all_missing_files_shows_warning_and_empty_grid(self):
        self._snap('emp/a.jpg', minutes_ago=10)
        self._snap('emp/b.jpg', minutes_ago=5)

        response = self.client.get('/manage/snapshots/?username=emp')
        self.assertEqual(response.context['snapshots'], [])
        self.assertEqual(response.context['missing_count'], 2)
        self.assertIn('موجود نیست', response.content.decode())

    def test_missing_snapshot_image_returns_placeholder(self):
        snap = self._snap('emp/gone.jpg')
        response = self.client.get('/manage/snapshots/image/%d/' % snap.id)
        self.assertEqual(response.status_code, 200)
        self.assertIn('image/svg+xml', response['Content-Type'])
        self.assertIn('بدون عکس', response.content.decode())

    def test_existing_snapshot_image_returns_jpeg(self):
        payload = b'\xff\xd8\xff\xe0fake-jpeg\xff\xd9'
        snap = self._snap('emp/ok.jpg')
        self._file('emp/ok.jpg', payload)

        response = self.client.get('/manage/snapshots/image/%d/' % snap.id)
        self.assertEqual(response.status_code, 200)
        self.assertIn('image/jpeg', response['Content-Type'])
        self.assertEqual(b''.join(response.streaming_content), payload)

    def test_orphan_cleanup_does_not_delete_recent_files(self):
        from adminpanel.diagnostics import fix_orphan_snapshot_files

        # حداقل یک رکورد واقعی داشته باشیم تا محافظ «دیتابیس خالی» فعال نشود.
        snap = self._snap('emp/known.jpg', minutes_ago=5)
        self._file('emp/known.jpg')

        recent = self._file('emp/orphan-recent.jpg')
        old = self._file('emp/orphan-old.jpg')
        two_days_ago = time.time() - 2 * 24 * 3600
        os.utime(old, (two_days_ago, two_days_ago))

        removed, _message = fix_orphan_snapshot_files()

        self.assertEqual(removed, 1)
        self.assertTrue(os.path.isfile(recent), 'فایل تازه نباید حذف شود')
        self.assertFalse(os.path.isfile(old), 'فایل قدیمی یتیم باید حذف شود')
        self.assertTrue(os.path.isfile(os.path.join(
            snapshots_root(), snap.photo,
        )), 'فایل دارای رکورد نباید حذف شود')

    def test_orphan_cleanup_refuses_empty_database(self):
        from adminpanel.diagnostics import fix_orphan_snapshot_files

        old = self._file('emp/orphan.jpg')
        two_days_ago = time.time() - 2 * 24 * 3600
        os.utime(old, (two_days_ago, two_days_ago))

        removed, message = fix_orphan_snapshot_files()
        self.assertEqual(removed, 0)
        self.assertIn('پاک‌سازی انجام نشد', message)
        self.assertTrue(os.path.isfile(old))

    def test_orphan_check_ignores_recent_files(self):
        from adminpanel.diagnostics import check_orphan_snapshot_files

        self._file('emp/recent.jpg')
        old = self._file('emp/old.jpg')
        two_days_ago = time.time() - 2 * 24 * 3600
        os.utime(old, (two_days_ago, two_days_ago))

        count, _message = check_orphan_snapshot_files()
        self.assertEqual(count, 1)
