from django.core.management.base import BaseCommand

from recognition.checkout_errors import auto_fix_missing_checkouts


class Command(BaseCommand):
    help = 'ثبت خودکار خروج ساعت ۲۴ برای افرادی که خروج نزده‌اند (ایجاد خطای خروج برای مدیریت).'

    def handle(self, *args, **options):
        created = auto_fix_missing_checkouts()
        self.stdout.write(self.style.SUCCESS(
            f'{created} خطای خروج ایجاد شد.'))