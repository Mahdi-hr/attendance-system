"""اجرای ربات بله با Long Polling.

    python manage.py runbalebot
    python manage.py runbalebot --once        # فقط یک دور (برای تست)
    python manage.py runbalebot --timeout 25

این دستور را سرویس systemd یا cron زنده نگه می‌دارد. هیچ پورتی باز نمی‌شود
و به وب‌سرور کاری ندارد.
"""

import logging
import signal
import time

from django.core.management.base import BaseCommand, CommandError

from balebot.client import BaleClient, BaleError
from balebot.handlers import handle_update
from balebot.models import BotState

logger = logging.getLogger('balebot')


class Command(BaseCommand):
    help = 'دریافت پیام‌های بازوی بله و پاسخ‌دهی (Long Polling)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--once', action='store_true',
            help='فقط یک بار getUpdates را صدا بزن و خارج شو.',
        )
        parser.add_argument(
            '--timeout', type=int, default=25,
            help='مدت Long Polling هر درخواست (ثانیه). پیش‌فرض ۲۵.',
        )
        parser.add_argument(
            '--max-updates', type=int, default=0,
            help='حداکثر تعداد update سپس خروج (۰ = بی‌نهایت).',
        )

    def handle(self, *args, **options):
        try:
            client = BaleClient()
        except BaleError as exc:
            raise CommandError(str(exc))

        try:
            me = client.get_me()
        except BaleError as exc:
            raise CommandError('اتصال به بله برقرار نشد: %s' % exc)

        self.stdout.write(self.style.SUCCESS(
            'ربات متصل شد: @%s (%s)' % (
                me.get('username', '?'), me.get('first_name', ''),
            )
        ))

        state, _ = BotState.objects.get_or_create(key='updates')
        stop = {'flag': False}

        def _stop(signum, frame):
            stop['flag'] = True
            self.stdout.write('در حال توقف ربات…')

        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)

        processed = 0
        while not stop['flag']:
            try:
                updates = client.get_updates(
                    offset=state.last_update_id + 1,
                    timeout=options['timeout'],
                )
            except BaleError as exc:
                self.stderr.write('خطای دریافت پیام: %s' % exc)
                time.sleep(5)
                continue

            for update in updates:
                update_id = update.get('update_id')
                try:
                    handle_update(update, client)
                except Exception:
                    logger.exception('خطا در پردازش update %s', update_id)
                    self.stderr.write('خطا در پردازش update %s' % update_id)

                if update_id is not None:
                    state.last_update_id = int(update_id)
                    state.save(update_fields=['last_update_id', 'updated_at'])

                processed += 1
                if options['max_updates'] and processed >= options['max_updates']:
                    return

            if options['once']:
                return
