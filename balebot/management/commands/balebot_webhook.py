"""مدیریت وبهوک ربات بله."""

from django.core.management.base import BaseCommand, CommandError

from balebot.client import BaleClient, BaleError


class Command(BaseCommand):
    help = 'تنظیم/حذف/بررسی وبهوک ربات بله'

    def add_arguments(self, parser):
        parser.add_argument('action', choices=['set', 'delete', 'info'])
        parser.add_argument(
            'url', nargs='?', default='',
            help='آدرس HTTPS وبهوک (فقط برای action=set)',
        )

    def handle(self, *args, **options):
        try:
            client = BaleClient()
        except BaleError as exc:
            raise CommandError(str(exc))

        action = options['action']
        if action == 'set':
            url = options['url']
            if not url.startswith('https://'):
                raise CommandError(
                    'وبهوک بله باید با HTTPS و روی پورت ۴۴۳ یا ۸۸ باشد.'
                )
            try:
                client.call('setWebhook', url=url)
            except BaleError as exc:
                raise CommandError('تنظیم وبهوک ناموفق: %s' % exc)
            self.stdout.write(self.style.SUCCESS('وبهوک تنظیم شد: %s' % url))
            return

        if action == 'delete':
            try:
                client.call('deleteWebhook')
            except BaleError as exc:
                raise CommandError('حذف وبهوک ناموفق: %s' % exc)
            self.stdout.write(self.style.SUCCESS('وبهوک حذف شد.'))
            return

        try:
            info = client.call('getWebhookInfo')
        except BaleError as exc:
            raise CommandError('دریافت وضعیت وبهوک ناموفق: %s' % exc)
        self.stdout.write(str(info))
