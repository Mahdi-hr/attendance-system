"""
Custom middleware for performance optimization and security headers.
"""
import re
from django.utils.deprecation import MiddlewareMixin


class CacheControlMiddleware(MiddlewareMixin):
    """
    Add Cache-Control headers for static assets and API responses.
    """
    STATIC_PATTERNS = [
        re.compile(r'^/static/'),
        re.compile(r'^/media/'),
    ]
    API_PATTERNS = [
        re.compile(r'^/api/'),
    ]
    NO_CACHE_PATTERNS = [
        re.compile(r'^/admin/'),
        re.compile(r'^/login/'),
        re.compile(r'^/logout/'),
        re.compile(r'^/mark-attendance/'),
        re.compile(r'^/register/'),
    ]

    def process_response(self, request, response):
        path = request.path_info

        # No cache for sensitive pages
        if any(p.match(path) for p in self.NO_CACHE_PATTERNS):
            response['Cache-Control'] = 'no-store, no-cache, must-revalidate, private'
            response['Pragma'] = 'no-cache'
            response['Expires'] = '0'
            return response

        # Long-term cache for static assets (1 year)
        if any(p.match(path) for p in self.STATIC_PATTERNS):
            if 'Cache-Control' not in response:
                response['Cache-Control'] = 'public, max-age=31536000, immutable'
            return response

        # API responses — هرگز نباید کش شوند.
        # (مخصوصاً /api/current_time/ که هر ثانیه از آن استفاده می‌شود؛
        #  اگر کش شود، ساعت هدر در مرورگر ثابت می‌ماند.)
        if any(p.match(path) for p in self.API_PATTERNS):
            response['Cache-Control'] = 'no-store, no-cache, must-revalidate, private'
            response['Pragma'] = 'no-cache'
            response['Expires'] = '0'
            return response

        # ⚠️ همهٔ صفحات داینامیک (از جمله داشبورد، دکمهٔ تغییر حالت و
        # پاسخ ۳۰۲ آن) وابسته به نشست/نقش کاربر هستند و به‌هیچ‌وجه نباید
        # در مرورگر کش شوند. در نسخهٔ قبلی اینجا `private, max-age=60`
        # گذاشته شده بود و باعث می‌شد بعد از چند بار تغییر حالت، مرورگر
        # همیشه نسخهٔ قدیمی (ریدایرکت/صفحهٔ کش‌شده) را نشان دهد و دکمه
        # «گیر کند». حالا هیچ چیز داینامیکی کش نمی‌شود.
        response['Cache-Control'] = 'no-store, no-cache, must-revalidate, private'
        response['Pragma'] = 'no-cache'
        response['Expires'] = '0'

        return response


class SecurityHeadersMiddleware(MiddlewareMixin):
    """
    Add additional security headers.
    """
    def process_response(self, request, response):
        # Prevent MIME type sniffing
        response['X-Content-Type-Options'] = 'nosniff'

        # XSS Protection
        response['X-XSS-Protection'] = '1; mode=block'

        # Referrer Policy
        response['Referrer-Policy'] = 'strict-origin-when-cross-origin'

        # Permissions Policy (Feature Policy)
        response['Permissions-Policy'] = (
            'accelerometer=(), camera=(), geolocation=(), gyroscope=(), '
            'magnetometer=(), microphone=(), payment=(), usb=()'
        )

        # Cross-Origin policies
        response['Cross-Origin-Opener-Policy'] = 'same-origin'
        response['Cross-Origin-Resource-Policy'] = 'same-origin'

        return response


class PreloadHeadersMiddleware(MiddlewareMixin):
    """
    Add Link headers for HTTP/2 Server Push / Preload hints.
    """
    PRELOAD_RESOURCES = [
        ('/static/recognition/css/bootstrap.rtl.min.css', 'style'),
        ('/static/recognition/fonts/vazirmatn/vazirmatn.css', 'style'),
        ('/static/recognition/css/fontawesome.min.css', 'style'),
        ('/static/recognition/fonts/webfonts/Vazirmatn-Regular.woff2', 'font'),
        ('/static/recognition/fonts/webfonts/Vazirmatn-Medium.woff2', 'font'),
        ('/static/recognition/fonts/webfonts/Vazirmatn-Bold.woff2', 'font'),
        ('/static/recognition/webfonts/fa-solid-900.woff2', 'font'),
        ('/static/recognition/webfonts/fa-regular-400.woff2', 'font'),
    ]

    def process_response(self, request, response):
        # Add preload Link headers for HTML GET responses.
        # (چک Sec-Fetch-Dest حذف شد: هم در تست‌کلاینت هدر موجود نیست،
        #  هم برای هر درخواست HTML عادی preload مفید است.)
        if (request.method == 'GET' and
            response.get('Content-Type', '').startswith('text/html') and
            'text/html' in response.get('Content-Type', '')):
            # Add preload Link headers
            link_header = ', '.join(
                f'<{url}>; rel=preload; as={as_type}{"; crossorigin" if as_type == "font" else ""}'
                for url, as_type in self.PRELOAD_RESOURCES
            )
            response['Link'] = link_header

        return response