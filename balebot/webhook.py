"""وبهوک اختیاری ربات بله.

به‌صورت پیش‌فرض ربات با Long Polling کار می‌کند و این مسیر غیرفعال است.
اگر بعداً دامنهٔ HTTPS (پورت ۴۴۳ یا ۸۸) داشتید:

    BALE_WEBHOOK_SECRET=<یک-رشته-تصادفی>
    python manage.py balebot_webhook set https://example.com/balebot/webhook/<secret>/

و سرویس Long Polling را خاموش کنید تا دو روش هم‌زمان اجرا نشوند.
"""

import json
import logging

from django.conf import settings
from django.http import HttpResponse, HttpResponseForbidden
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .client import BaleClient
from .handlers import handle_update

logger = logging.getLogger('balebot')


@csrf_exempt
@require_POST
def bale_webhook(request, secret):
    expected = getattr(settings, 'BALE_WEBHOOK_SECRET', '')
    if not expected or secret != expected:
        return HttpResponseForbidden('forbidden')

    try:
        update = json.loads(request.body.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return HttpResponse('bad request', status=400)

    try:
        handle_update(update, BaleClient())
    except Exception:
        logger.exception('خطا در پردازش وبهوک بله')

    # بله منتظر پاسخ 200 است؛ خطای داخلی نباید باعث ارسال دوباره شود.
    return HttpResponse('ok')
