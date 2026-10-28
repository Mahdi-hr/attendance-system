"""ارسال پیام‌های زمان‌بندی‌شدهٔ ربات بله (برای cron هر دقیقه)."""

from django.core.management.base import BaseCommand, CommandError

from balebot.client import BaleClient, BaleError
from balebot.services import send_scheduled_due


class Command(BaseCommand):
    help = 'ارسال پیام‌های زمان‌بندی‌شدهٔ ربات بله'

    def handle(self, *args, **options):
        try:
            client = BaleClient()
        except BaleError as exc:
            raise CommandError(str(exc))
        try:
            count = send_scheduled_due(client)
        except BaleError as exc:
            raise CommandError('ارسال پیام زمان‌بندی‌شده ناموفق: %s' % exc)
        self.stdout.write('پیام‌های زمان‌بندی‌شدهٔ ارسال‌شده: %d' % count)
