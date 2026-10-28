"""کلاینت سبک API بازوی بله.

مستندات: https://docs.bale.ai/ — آدرس هر متد:
    https://tapi.bale.ai/bot<token>/<method>

API بله سازگار با API بات تلگرام است. برای کم‌کردن وابستگی‌ها از
`urllib` استاندارد پایتون استفاده شده است.
"""

import json
import urllib.error
import urllib.request
import uuid

from django.conf import settings


class BaleError(Exception):
    """خطای API یا شبکهٔ بله."""

    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


class BaleClient:
    """ارتباط با API بله."""

    def __init__(self, token=None, base_url=None):
        self.token = token or getattr(settings, 'BALE_BOT_TOKEN', '')
        self.base_url = (
            base_url or getattr(settings, 'BALE_API_BASE', 'https://tapi.bale.ai')
        ).rstrip('/')
        if not self.token:
            raise BaleError('توکن ربات بله تنظیم نشده است (BALE_BOT_TOKEN).')

    # ------------------------------------------------------------------
    # زیرساخت درخواست
    # ------------------------------------------------------------------
    def _request(self, request, http_timeout=40):
        try:
            with urllib.request.urlopen(request, timeout=http_timeout) as response:
                raw = response.read().decode('utf-8')
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode('utf-8', 'replace') or '{}'
            try:
                payload = json.loads(raw)
            except ValueError:
                payload = {}
            raise BaleError(
                payload.get('description') or 'خطای HTTP %s' % exc.code,
                payload.get('error_code') or exc.code,
            )
        except (urllib.error.URLError, OSError) as exc:
            raise BaleError('خطای شبکه در ارتباط با بله: %s' % exc)

        try:
            payload = json.loads(raw)
        except ValueError:
            raise BaleError('پاسخ نامعتبر از سرور بله دریافت شد.')

        if not payload.get('ok'):
            raise BaleError(
                payload.get('description') or 'خطای نامشخص API بله',
                payload.get('error_code'),
            )
        return payload.get('result')

    def call(self, method, http_timeout=40, **params):
        """یک متد JSON API را صدا می‌زند و `result` را برمی‌گرداند."""
        url = '%s/bot%s/%s' % (self.base_url, self.token, method)
        body = json.dumps(params, ensure_ascii=False).encode('utf-8')
        request = urllib.request.Request(
            url,
            data=body,
            headers={'Content-Type': 'application/json; charset=utf-8'},
            method='POST',
        )
        return self._request(request, http_timeout=http_timeout)

    # ------------------------------------------------------------------
    # متدهای پرکاربرد
    # ------------------------------------------------------------------
    def get_me(self):
        return self.call('getMe')

    def get_updates(self, offset=0, timeout=25, limit=100):
        # http_timeout باید از timeout خود API بیشتر باشد.
        return self.call(
            'getUpdates',
            http_timeout=timeout + 15,
            offset=offset,
            timeout=timeout,
            limit=limit,
        ) or []

    def send_message(self, chat_id, text, reply_markup=None):
        params = {'chat_id': chat_id, 'text': text}
        if reply_markup:
            params['reply_markup'] = reply_markup
        return self.call('sendMessage', **params)

    def edit_message_text(self, chat_id, message_id, text, reply_markup=None):
        params = {'chat_id': chat_id, 'message_id': message_id, 'text': text}
        if reply_markup:
            params['reply_markup'] = reply_markup
        return self.call('editMessageText', **params)

    def edit_message_reply_markup(self, chat_id, message_id, reply_markup=None):
        params = {'chat_id': chat_id, 'message_id': message_id}
        if reply_markup:
            params['reply_markup'] = reply_markup
        return self.call('editMessageReplyMarkup', **params)

    def answer_callback_query(self, callback_query_id, text=None):
        params = {'callback_query_id': callback_query_id}
        if text:
            params['text'] = text
        return self.call('answerCallbackQuery', **params)

    def delete_message(self, chat_id, message_id):
        return self.call('deleteMessage', chat_id=chat_id, message_id=message_id)

    def send_document(self, chat_id, filename, content, caption=None,
                      mime_type='application/octet-stream'):
        """ارسال فایل (مثلاً xlsx) با multipart/form-data."""
        boundary = '----BaleBot%s' % uuid.uuid4().hex
        parts = []

        def _field(name, value):
            parts.append((
                '--%s\r\n'
                'Content-Disposition: form-data; name="%s"\r\n\r\n'
                '%s\r\n' % (boundary, name, value)
            ).encode('utf-8'))

        _field('chat_id', str(chat_id))
        if caption:
            _field('caption', caption)

        parts.append((
            '--%s\r\n'
            'Content-Disposition: form-data; name="document"; filename="%s"\r\n'
            'Content-Type: %s\r\n\r\n'
            % (boundary, filename, mime_type)
        ).encode('utf-8'))
        parts.append(content)
        parts.append(('\r\n--%s--\r\n' % boundary).encode('utf-8'))

        url = '%s/bot%s/sendDocument' % (self.base_url, self.token)
        request = urllib.request.Request(
            url,
            data=b''.join(parts),
            headers={
                'Content-Type': 'multipart/form-data; boundary=%s' % boundary,
            },
            method='POST',
        )
        return self._request(request, http_timeout=60)

    def send_photo(self, chat_id, filename, content, caption=None,
                   mime_type='image/jpeg', reply_markup=None):
        """ارسال عکس (برای بازبینی عکس لحظه در ربات)."""
        boundary = '----BaleBot%s' % uuid.uuid4().hex
        parts = []

        def _field(name, value):
            parts.append((
                '--%s\r\n'
                'Content-Disposition: form-data; name="%s"\r\n\r\n'
                '%s\r\n' % (boundary, name, value)
            ).encode('utf-8'))

        _field('chat_id', str(chat_id))
        if caption:
            _field('caption', caption)
        if reply_markup:
            _field('reply_markup', json.dumps(reply_markup, ensure_ascii=False))

        parts.append((
            '--%s\r\n'
            'Content-Disposition: form-data; name="photo"; filename="%s"\r\n'
            'Content-Type: %s\r\n\r\n'
            % (boundary, filename, mime_type)
        ).encode('utf-8'))
        parts.append(content)
        parts.append(('\r\n--%s--\r\n' % boundary).encode('utf-8'))

        url = '%s/bot%s/sendPhoto' % (self.base_url, self.token)
        request = urllib.request.Request(
            url,
            data=b''.join(parts),
            headers={
                'Content-Type': 'multipart/form-data; boundary=%s' % boundary,
            },
            method='POST',
        )
        return self._request(request, http_timeout=60)
