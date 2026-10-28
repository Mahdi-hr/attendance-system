"""ارسال یادآورهای ربات بله (خروج فراموش‌شده، مأموریت نزدیک، صف تأیید)."""

from django.core.management.base import BaseCommand, CommandError

from balebot.client import BaleClient, BaleError
from balebot.reminders import run_reminders


class Command(BaseCommand):
    help = 'ارسال یادآورهای خودکار ربات بله'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='فقط تعداد کاندیدها را نشان بده؛ چیزی ارسال یا ثبت نکن.',
        )

    def handle(self, *args, **options):
        if options['dry_run']:
            stats = run_reminders(None, dry_run=True)
            self.stdout.write('آزمایشی (بدون ارسال): %s' % stats)
            return

        try:
            client = BaleClient()
        except BaleError as exc:
            raise CommandError(str(exc))

        try:
            stats = run_reminders(client)
        except BaleError as exc:
            raise CommandError('ارسال یادآور ناموفق: %s' % exc)

        self.stdout.write(
            'یادآورها — خروج: %(checkout)s | مأموریت نزدیک: %(upcoming)s | '
            'صف تأیید: %(pending)s' % stats
        )
