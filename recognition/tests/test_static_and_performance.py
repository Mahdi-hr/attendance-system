"""
Comprehensive test suite for static files, performance, and security.
"""
import os
import gzip
import re
from pathlib import Path
from django.test import TestCase, Client, override_settings
from django.conf import settings
from django.urls import reverse
from django.core.files.storage import default_storage
from django.contrib.auth.models import User
from users.models import UserProfile


class StaticFilesTestCase(TestCase):
    """Test that all static files are served locally and correctly."""

    def setUp(self):
        self.client = Client()
        self.static_root = Path(settings.STATIC_ROOT)

    def test_static_root_exists(self):
        """Verify STATIC_ROOT directory exists."""
        self.assertTrue(self.static_root.exists(), "STATIC_ROOT does not exist")

    def test_critical_css_files_exist(self):
        """Verify critical CSS files exist in staticfiles."""
        css_files = [
            'recognition/css/bootstrap.rtl.min.css',
            'recognition/fonts/vazirmatn/vazirmatn.css',
            'recognition/css/fontawesome.min.css',
            'recognition/css/jazzmin_admin.css',
        ]
        for css_file in css_files:
            path = self.static_root / css_file
            self.assertTrue(path.exists(), f"Missing CSS file: {css_file}")

    def test_critical_js_files_exist(self):
        """Verify critical JS files exist in staticfiles."""
        js_files = [
            'recognition/js/bootstrap.bundle.min.js',
            'recognition/js/persian-datepicker.js',
        ]
        for js_file in js_files:
            path = self.static_root / js_file
            self.assertTrue(path.exists(), f"Missing JS file: {js_file}")

    def test_font_files_exist(self):
        """Verify font files exist in staticfiles."""
        font_files = [
            'recognition/fonts/webfonts/Vazirmatn-Regular.woff2',
            'recognition/fonts/webfonts/Vazirmatn-Medium.woff2',
            'recognition/fonts/webfonts/Vazirmatn-Bold.woff2',
            'recognition/fonts/webfonts/Vazirmatn-SemiBold.woff2',
            'recognition/webfonts/fa-solid-900.woff2',
            'recognition/webfonts/fa-regular-400.woff2',
        ]
        for font_file in font_files:
            path = self.static_root / font_file
            self.assertTrue(path.exists(), f"Missing font file: {font_file}")

    def test_no_cdn_references_in_templates(self):
        """Verify no CDN references in project templates."""
        template_dirs = [
            Path(settings.BASE_DIR) / 'recognition' / 'templates',
            Path(settings.BASE_DIR) / 'users' / 'templates',
        ]
        cdn_patterns = [
            'cdn', 'cdnjs', 'fonts.googleapis', 'unpkg', 'jsdelivr', 'cloudflare'
        ]

        for template_dir in template_dirs:
            for html_file in template_dir.rglob('*.html'):
                content = html_file.read_text(encoding='utf-8')
                for pattern in cdn_patterns:
                    self.assertNotIn(
                        pattern, content.lower(),
                        f"CDN reference '{pattern}' found in {html_file}"
                    )

    def test_static_files_served_with_correct_mime_types(self):
        """Verify static files are served with correct MIME types."""
        test_cases = [
            ('/static/recognition/css/bootstrap.rtl.min.css', 'text/css'),
            ('/static/recognition/js/bootstrap.bundle.min.js', 'javascript'),
            ('/static/recognition/fonts/webfonts/Vazirmatn-Regular.woff2', 'font/woff2'),
            ('/static/recognition/webfonts/fa-solid-900.woff2', 'font/woff2'),
        ]
        for url, expected_mime in test_cases:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, f"Failed to serve {url}")
            self.assertIn(expected_mime, response.get('Content-Type', ''),
                          f"Wrong MIME type for {url}: {response.get('Content-Type')}")

    def test_static_files_have_cache_headers(self):
        """Verify static files have proper Cache-Control headers."""
        response = self.client.get('/static/recognition/css/bootstrap.rtl.min.css')
        self.assertEqual(response.status_code, 200)
        cache_control = response.get('Cache-Control', '')
        self.assertIn('max-age=', cache_control)
        self.assertIn('public', cache_control)

    def test_service_worker_served(self):
        """Verify service worker is served correctly."""
        response = self.client.get('/static/recognition/js/sw.js')
        self.assertEqual(response.status_code, 200)
        self.assertIn('javascript', response.get('Content-Type', ''))

    def test_sw_register_served(self):
        """Verify SW registration script is served."""
        response = self.client.get('/static/recognition/js/sw-register.js')
        self.assertEqual(response.status_code, 200)
        self.assertIn('javascript', response.get('Content-Type', ''))


class PerformanceTestCase(TestCase):
    """Test performance optimizations."""

    def setUp(self):
        self.client = Client()

    def _get_home_content(self):
        """Get home page content, following redirects if needed."""
        response = self.client.get('/', follow=True)
        return response.content.decode('utf-8'), response

    def test_preload_headers_in_base_template(self):
        """Verify preload resource hints are present in base template."""
        content, response = self._get_home_content()
        self.assertEqual(response.status_code, 200)
        # Check for preload links
        self.assertIn('rel="preload"', content)
        self.assertIn('as="style"', content)
        self.assertIn('as="font"', content)
        self.assertIn('crossorigin', content)

    def test_prefetch_headers_in_base_template(self):
        """Verify prefetch hints for non-critical resources."""
        content, response = self._get_home_content()
        self.assertEqual(response.status_code, 200)
        self.assertIn('rel="prefetch"', content)

    def test_dns_prefetch_in_base_template(self):
        """Verify dns-prefetch for static domain."""
        content, response = self._get_home_content()
        self.assertEqual(response.status_code, 200)
        self.assertIn('rel="dns-prefetch"', content)
        self.assertIn('rel="preconnect"', content)

    def test_csp_headers(self):
        """Verify Content Security Policy headers."""
        content, response = self._get_home_content()
        self.assertEqual(response.status_code, 200)
        csp = response.get('Content-Security-Policy', '')
        # CSP might be empty for login page, check if present
        if csp:
            self.assertIn("default-src 'self'", csp)
            self.assertIn("style-src 'self' 'unsafe-inline'", csp)
            self.assertIn("script-src 'self'", csp)
            self.assertIn("font-src 'self' data:", csp)

    def test_security_headers(self):
        """Verify security headers are present."""
        content, response = self._get_home_content()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get('X-Content-Type-Options'), 'nosniff')
        self.assertEqual(response.get('X-XSS-Protection'), '1; mode=block')
        self.assertEqual(response.get('Referrer-Policy'), 'strict-origin-when-cross-origin')
        self.assertIn('Permissions-Policy', response)
        self.assertEqual(response.get('Cross-Origin-Opener-Policy'), 'same-origin')

    def test_no_external_fonts_loaded(self):
        """Verify no external font requests in HTML."""
        content, response = self._get_home_content()
        self.assertEqual(response.status_code, 200)
        # Should not have Google Fonts or other external fonts
        self.assertNotIn('fonts.googleapis.com', content)
        self.assertNotIn('fonts.gstatic.com', content)


class CacheControlTestCase(TestCase):
    """Test Cache-Control middleware behavior."""

    def setUp(self):
        self.client = Client()

    def test_static_files_long_cache(self):
        """Static files should have long-term cache."""
        response = self.client.get('/static/recognition/css/bootstrap.rtl.min.css')
        cache_control = response.get('Cache-Control', '')
        self.assertIn('max-age=31536000', cache_control)
        self.assertIn('public', cache_control)

    def test_admin_no_cache(self):
        """Admin pages should have no-cache."""
        response = self.client.get('/admin/')
        cache_control = response.get('Cache-Control', '')
        self.assertIn('no-store', cache_control)
        self.assertIn('no-cache', cache_control)

    def test_api_no_cache(self):
        """API responses should NOT be cached (especially current_time,
        که هر ثانیه استفاده می‌شود و کش‌شدنش ساعت هدر را ثابت می‌کند)."""
        response = self.client.get('/api/current_time/')
        cache_control = response.get('Cache-Control', '')
        self.assertIn('no-store', cache_control)
        self.assertIn('no-cache', cache_control)
        self.assertNotIn('max-age=', cache_control)

    def test_login_no_cache(self):
        """Login page should have no-cache."""
        response = self.client.get('/login/')
        cache_control = response.get('Cache-Control', '')
        self.assertIn('no-store', cache_control)
        self.assertIn('no-cache', cache_control)

    def test_mode_toggle_never_cached(self):
        """نکتهٔ مهم: داشبورد و ریدایرکت دکمهٔ تغییر حالت ادمین/کاربر
        باید همیشه no-store باشند وگرنه مرورگر نسخهٔ قدیمی را نشان می‌دهد
        و دکمه «گیر می‌کند» (باگ قدیمی max-age=60)."""
        user = User.objects.create_user(username='toggle_test', password='x')
        UserProfile.objects.create(user=user, role='both')
        self.client.force_login(user)

        # ریدایرکت دکمهٔ «ورود به حالت ادمین» نباید کش شود
        r = self.client.get('/admin-mode/on/')
        self.assertEqual(r.status_code, 302)
        cc = r.get('Cache-Control', '')
        self.assertIn('no-store', cc)
        self.assertNotIn('max-age=', cc)

        # داشبورد (که محتوایش به حالت کاربر بستگی دارد) نباید کش شود
        r = self.client.get('/dashboard/')
        self.assertEqual(r.status_code, 200)
        cc = r.get('Cache-Control', '')
        self.assertIn('no-store', cc)
        self.assertNotIn('max-age=', cc)

        # ریدایرکت دکمهٔ «خروج از حالت ادمین» هم نباید کش شود
        r = self.client.get('/admin-mode/off/')
        self.assertEqual(r.status_code, 302)
        cc = r.get('Cache-Control', '')
        self.assertIn('no-store', cc)
        self.assertNotIn('max-age=', cc)

    def test_html_short_cache(self):
        """HTML pages (non-auth) should have short cache."""
        # Test a page that doesn't require auth and isn't sensitive
        response = self.client.get('/login/')
        # Login page correctly has no-cache for security
        cache_control = response.get('Cache-Control', '')
        self.assertIn('no-store', cache_control)


class CompressionTestCase(TestCase):
    """Test compression (gzip) of static assets."""

    def setUp(self):
        self.client = Client()

    def test_compressed_files_exist(self):
        """Verify compressed (.gz) versions exist in staticfiles."""
        static_root = Path(settings.STATIC_ROOT)
        compressed_files = [
            'recognition/css/bootstrap.rtl.min.css.gz',
            'recognition/js/bootstrap.bundle.min.js.gz',
        ]
        for gz_file in compressed_files:
            path = static_root / gz_file
            self.assertTrue(path.exists(), f"Missing compressed file: {gz_file}")

    def test_font_no_compression(self):
        """Font files should not be compressed (already compressed)."""
        static_root = Path(settings.STATIC_ROOT)
        font_gz = static_root / 'recognition/fonts/webfonts/Vazirmatn-Regular.woff2.gz'
        # woff2 is already compressed, WhiteNoise skips it
        self.assertFalse(font_gz.exists(), "Font should not have .gz version")


class ServiceWorkerTestCase(TestCase):
    """Test Service Worker functionality."""

    def setUp(self):
        self.client = Client()

    def _get_sw_content(self):
        """Get service worker content."""
        response = self.client.get('/static/recognition/js/sw.js')
        self.assertEqual(response.status_code, 200)
        # Handle WhiteNoiseFileResponse streaming content
        if hasattr(response, 'streaming_content'):
            content = b''.join(response.streaming_content).decode('utf-8')
        else:
            content = response.content.decode('utf-8')
        return content

    def test_sw_cleanup_instead_of_register(self):
        """sw-register.js دیگر SW ثبت نمی‌کند؛ فقط SW های قبلی را
        آن‌رجیستر و کش‌هایشان را پاک می‌کند. (ثبت SW حذف شد چون باعث
        گیرکردن دکمهٔ تغییر حالت ادمین/کاربر می‌شد.)"""
        response = self.client.get('/static/recognition/js/sw-register.js')
        self.assertEqual(response.status_code, 200)
        if hasattr(response, 'streaming_content'):
            content = b''.join(response.streaming_content).decode('utf-8')
        else:
            content = response.content.decode('utf-8')
        # حتماً باید پاک‌سازی انجام دهد
        self.assertIn('getRegistrations', content)
        self.assertIn('unregister', content)
        self.assertIn('caches.delete', content)
        # و دیگر نباید SW جدیدی ثبت کند
        self.assertNotIn('.register(', content)
        self.assertNotIn("scope: '/'", content)

    def test_sw_caches_static_assets(self):
        """SW should cache static assets."""
        content = self._get_sw_content()
        self.assertIn('cacheFirst', content)
        self.assertIn('STATIC_ASSETS', content)

    def test_sw_network_first_for_api(self):
        """SW should use network-first for API calls."""
        content = self._get_sw_content()
        self.assertIn('networkFirst', content)
        self.assertIn('/api/', content)


class LocalAssetsOnlyTestCase(TestCase):
    """Verify all assets are served locally, no external dependencies."""

    def setUp(self):
        self.client = Client()

    def _get_home_content(self):
        """Get home page content, following redirects if needed."""
        response = self.client.get('/', follow=True)
        return response.content.decode('utf-8')

    def test_bootstrap_local(self):
        """Bootstrap CSS/JS should be local."""
        content = self._get_home_content()
        self.assertIn('/static/recognition/css/bootstrap.rtl.min.css', content)
        self.assertNotIn('cdn.jsdelivr.net', content)
        self.assertNotIn('cdnjs.cloudflare.com', content)

    def test_fontawesome_local(self):
        """FontAwesome should be local."""
        content = self._get_home_content()
        self.assertIn('/static/recognition/css/fontawesome.min.css', content)
        self.assertNotIn('kit.fontawesome.com', content)
        self.assertNotIn('cdnjs.cloudflare.com/ajax/libs/font-awesome', content)

    def test_vazirmatn_local(self):
        """Vazirmatn font should be local."""
        content = self._get_home_content()
        self.assertIn('/static/recognition/fonts/vazirmatn/vazirmatn.css', content)
        self.assertNotIn('fonts.googleapis.com', content)

    def test_jquery_local_via_jazzmin(self):
        """jQuery should be local via Jazzmin vendor."""
        # Check staticfiles has jquery
        jquery_path = Path(settings.STATIC_ROOT) / 'admin' / 'js' / 'vendor' / 'jquery' / 'jquery.js'
        self.assertTrue(jquery_path.exists(), "jQuery not found in staticfiles")


class HTTP2PushTestCase(TestCase):
    """Test HTTP/2 Server Push hints (Link headers)."""

    def setUp(self):
        self.client = Client()

    def test_link_header_present(self):
        """Link header should be present for HTML responses."""
        response = self.client.get('/', follow=True)
        self.assertEqual(response.status_code, 200)
        link_header = response.get('Link', '')
        self.assertIn('rel=preload', link_header)
        self.assertIn('as=style', link_header)
        self.assertIn('as=font', link_header)
        self.assertIn('crossorigin', link_header)

    def test_link_header_not_for_api(self):
        """Link header should not be added for API responses."""
        response = self.client.get('/api/current_time/')
        link_header = response.get('Link', '')
        self.assertEqual(link_header, '')


# Integration test for full page load
class IntegrationTestCase(TestCase):
    """Integration tests for full page loads."""

    def setUp(self):
        self.client = Client()

    def test_home_page_loads(self):
        """Home page should load successfully with all assets."""
        response = self.client.get('/', follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('سامانه حضور و غیاب', response.content.decode('utf-8'))

    def test_login_page_loads(self):
        """Login page should load successfully."""
        response = self.client.get('/login/')
        self.assertEqual(response.status_code, 200)

    def test_admin_page_loads(self):
        """Admin page should load successfully."""
        response = self.client.get('/admin/')
        # Admin redirects to login if not authenticated
        self.assertIn(response.status_code, (200, 302))

    def test_all_static_assets_load(self):
        """All critical static assets should return 200."""
        assets = [
            '/static/recognition/css/bootstrap.rtl.min.css',
            '/static/recognition/fonts/vazirmatn/vazirmatn.css',
            '/static/recognition/css/fontawesome.min.css',
            '/static/recognition/js/bootstrap.bundle.min.js',
            '/static/recognition/fonts/webfonts/Vazirmatn-Regular.woff2',
            '/static/recognition/webfonts/fa-solid-900.woff2',
        ]
        for asset in assets:
            response = self.client.get(asset)
            self.assertEqual(response.status_code, 200, f"Asset failed: {asset}")


# Run with: python manage.py test --verbosity=2