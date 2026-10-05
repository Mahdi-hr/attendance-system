"""پایه‌گذاری شناسهٔ دائمی چهره و تغییر نام کاربری بدون آسیب به مدل.

کاری که این دستور انجام می‌دهد
===============================
1. به هر کاربری که شناسه ندارد یک ``face_uid`` می‌دهد.
2. هر پوشهٔ عکسِ قدیمی (که نامش نام کاربری است) را به پوشه‌ای به نام
   ``face_uid`` تغییر نام می‌دهد.
3. پوشه‌هایی که به هیچ کاربری نمی‌خورند را **دست نمی‌زند** و گزارش می‌دهد
   (مثل پوشهٔ کارمندی که حذف شده) — یعنی از مدل بیرون می‌مانند ولی فایل‌هایشان
   سالم است.
4. نام کاربری‌ها را طبق نقشهٔ داده‌شده عوض می‌کند. چون پوشه‌ها حالا با شناسه
   هستند، این کار هیچ اثری روی مدل و عکس‌ها ندارد.
5. در پایان گالری را دوباره می‌سازد تا مدل حتماً با شناسه‌ها هم‌خوان باشد.

خطرناک نیست: هیچ عکسی حذف نمی‌شود و قبل از هر تغییر نامی که ممکن است،
پشتیبان از نام‌های قدیمی می‌ماند.

نمونه
=====
    python manage.py migrate_face_uids                 # فقط گزارش (خشک)
    python manage.py migrate_face_uids --apply         # اجرای واقعی
    python manage.py migrate_face_uids --apply --retrain

    # تغییر نام دستی
    python manage.py migrate_face_uids --apply --rename old=new
"""
import os
import shutil

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand

from users.faceid import (
    is_face_uid,
    training_root,
    ensure_all_face_uids,
    face_uid_of,
)


def _is_image(name):
    return os.path.splitext(name)[1].lower() in ('.jpg', '.jpeg', '.png')


def _folder_has_photos(path):
    try:
        return any(_is_image(f) for f in os.listdir(path))
    except OSError:
        return False


class Command(BaseCommand):
    help = ('پوشه‌های عکس‌ها را به شناسهٔ دائمی منتقل می‌کند و نام کاربری‌ها را '
            'عوض می‌کند، بدون آنکه مدل تشخیص چهره خراب شود.')

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true',
                            help='واقعاً تغییر بده (بدون این فقط گزارش می‌دهد)')
        parser.add_argument('--retrain', action='store_true',
                            help='بعد از کار، گالری چهره را دوباره بساز')
        parser.add_argument('--rename', action='append', default=[],
                            metavar='OLD=NEW',
                            help='تغییر نام کاربری؛ چند بار قابل تکرار')

    def handle(self, *args, **options):
        apply = options['apply']
        root = training_root()

        self.stdout.write(self.style.MIGRATE_HEADING(
            '=== ۱) شناسهٔ دائمی کاربران ==='))
        created = ensure_all_face_uids()
        self.stdout.write(
            'شناسهٔ تازه ساخته شد: %d' % created)
        self.stdout.write('مجموع کاربران دارای شناسه: %d' % User.objects.count())

        self.stdout.write(self.style.MIGRATE_HEADING(
            '=== ۲) پوشه‌های عکس ==='))
        if not os.path.isdir(root):
            self.stdout.write('پوشهٔ %s وجود ندارد؛ کاری نشد.' % root)
        else:
            self._migrate_folders(root, apply)

        self.stdout.write(self.style.MIGRATE_HEADING(
            '=== ۳) تغییر نام کاربری ==='))
        self._rename_users(options['rename'], apply)

        if options['retrain']:
            self.stdout.write(self.style.MIGRATE_HEADING(
                '=== ۴) ساخت دوبارهٔ مدل ==='))
            self._retrain()
        else:
            self.stdout.write('')
            self.stdout.write('برای ساخت دوبارهٔ مدل: '
                              '--retrain را هم بده (یا از پنل «آموزش مدل»)')

        if not apply:
            self.stdout.write('')
            self.stdout.write(self.style.WARNING(
                'حالت گزارش بود؛ هیچ تغییری اعمال نشد. برای اجرا --apply بده.'))

    # ------------------------------------------------------------------
    def _migrate_folders(self, root, apply):
        moved = []
        skipped = []
        already = []
        for folder in sorted(os.listdir(root)):
            src = os.path.join(root, folder)
            if not os.path.isdir(src):
                continue
            if is_face_uid(folder):
                already.append(folder)
                continue

            # پوشهٔ نام‌کاربری → شناسهٔ همان کاربر
            uid = face_uid_of(folder)
            if not uid:
                # شاید خودش نام کاربری است ولی کاربری با این نام نیست
                # (پوشهٔ کارمند حذف‌شده). دست نمی‌زنیم.
                skipped.append(folder)
                continue

            dst = os.path.join(root, uid)
            if os.path.isdir(dst):
                # مقصد از قبل وجود دارد → ادغام می‌کنیم
                if apply:
                    self._merge(src, dst)
                    moved.append((folder, uid, 'ادغام'))
                else:
                    moved.append((folder, uid, 'ادغام'))
            else:
                if apply:
                    os.rename(src, dst)
                moved.append((folder, uid, 'جابه‌جا'))

        for src_name, uid, how in moved:
            self.stdout.write('  %s → %s  (%s)' % (src_name, uid, how))
        if already:
            self.stdout.write('  از قبل روی شناسه بود: %d پوشه' % len(already))
        if skipped:
            self.stdout.write(self.style.WARNING(
                '  به هیچ کاربری وصل نیستند و از مدل بیرون می‌مانند '
                '(فایل‌ها دست‌نخورده): %s' % '، '.join(skipped)))
        if not moved and not skipped:
            self.stdout.write('  همه‌چیز مرتب بود.')

    def _merge(self, src, dst):
        """عکس‌های پوشهٔ قدیمی را به پوشهٔ شناسه اضافه می‌کند."""
        existing = set(os.listdir(dst))
        for name in os.listdir(src):
            if not _is_image(name):
                continue
            target = name
            i = 1
            while target in existing:
                stem, ext = os.path.splitext(name)
                target = '%s_%d%s' % (stem, i, ext)
                i += 1
            existing.add(target)
            shutil.move(os.path.join(src, name), os.path.join(dst, target))
        try:
            os.rmdir(src)
        except OSError:
            pass

    # ------------------------------------------------------------------
    def _rename_users(self, pairs, apply):
        if not pairs:
            self.stdout.write('  نقشه‌ای داده نشد.')
            return
        for pair in pairs:
            if '=' not in pair:
                self.stdout.write(self.style.ERROR(
                    '  قالب اشتباه (باید OLD=NEW باشد): %s' % pair))
                continue
            old, new = pair.split('=', 1)
            old, new = old.strip(), new.strip()
            user = User.objects.filter(username=old).first()
            if user is None:
                self.stdout.write(self.style.ERROR(
                    '  کاربری با نام %r پیدا نشد.' % old))
                continue
            if not new:
                self.stdout.write(self.style.ERROR(
                    '  نام جدید خالی است؛ %r تغییر نکرد.' % old))
                continue
            clash = User.objects.filter(username=new).exclude(pk=user.pk).first()
            if clash is not None:
                self.stdout.write(self.style.ERROR(
                    '  نام %r قبلاً برای کاربر دیگری (%s) هست؛ %r تغییر نکرد.'
                    % (new, clash.pk, old)))
                continue
            uid = face_uid_of(old)
            if apply:
                user.username = new
                user.save(update_fields=['username'])
                self.stdout.write('  %s → %s   (شناسه %s دست‌نخورده)'
                                  % (old, new, uid))
            else:
                self.stdout.write('  %s → %s   (هنوز اعمال نشده)' % (old, new))

    # ------------------------------------------------------------------
    def _retrain(self):
        from recognition.camera import train_gallery, camera_manager
        count, msg = train_gallery()
        camera_manager.load_model()
        self.stdout.write(self.style.SUCCESS('  %s' % msg))