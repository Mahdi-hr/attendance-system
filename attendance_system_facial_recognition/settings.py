"""
Django settings for attendance_system_facial_recognition project.

🔒 Production-ready security configuration.
All external CDN dependencies removed - fully local assets.
"""

import os
import socket

# Build paths
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ============================================================
# SECURITY — Production hardened
# ============================================================
SECRET_KEY = os.environ.get(
    'DJANGO_SECRET_KEY',
    'django-insecure-change-this-in-production-please-use-env-var'
)

DEBUG = os.environ.get('DJANGO_DEBUG', '0') == '1'

# HTTPS toggle (set DJANGO_USE_HTTPS=1 in production with valid TLS)
USE_HTTPS = os.environ.get('DJANGO_USE_HTTPS', '0') == '1'

# Restrict to specific hosts in production
ALLOWED_HOSTS = os.environ.get('DJANGO_ALLOWED_HOSTS', '127.0.0.1,localhost,testserver').split(',')

# ============================================================
# CSRF — Strict origins only
# ============================================================
CSRF_TRUSTED_ORIGINS = [
    'http://127.0.0.1:8000',
    'http://localhost:8000',
    'http://192.168.12.116:8000',
]
if os.environ.get('DJANGO_CSRF_TRUSTED_ORIGINS'):
    CSRF_TRUSTED_ORIGINS += [
        o.strip() for o in os.environ['DJANGO_CSRF_TRUSTED_ORIGINS'].split(',')
        if o.strip()
    ]

CSRF_COOKIE_SECURE = USE_HTTPS
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = 'Strict' if USE_HTTPS else 'Lax'
CSRF_USE_SESSIONS = True
CSRF_COOKIE_NAME = 'csrftoken'
CSRF_HEADER_NAME = 'HTTP_X_CSRFTOKEN'
CSRF_FAILURE_VIEW = 'django.views.csrf.csrf_failure'

# ============================================================
# Session / Cookie — Secure (configurable for dev vs prod)
# ============================================================
SESSION_COOKIE_SECURE = USE_HTTPS
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Strict' if USE_HTTPS else 'Lax'
SESSION_COOKIE_AGE = 60 * 60 * 24 * 30   # 30 days (persistent login)
SESSION_EXPIRE_AT_BROWSER_CLOSE = False  # Keep session after browser close
SESSION_SAVE_EVERY_REQUEST = False       # Only save when modified

# ============================================================
# Application definition
# ============================================================
INSTALLED_APPS = [
    'jazzmin',
    'users.apps.UsersConfig',
    'recognition.apps.RecognitionConfig',

    'crispy_forms',
    'crispy_bootstrap4',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'whitenoise.runserver_nostatic',
    'django.contrib.staticfiles',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'attendance_system_facial_recognition.middleware.PreloadHeadersMiddleware',
    'attendance_system_facial_recognition.middleware.CacheControlMiddleware',
    'attendance_system_facial_recognition.middleware.SecurityHeadersMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'attendance_system_facial_recognition.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'users.context_processors.role_context',
            ],
        },
    },
]

WSGI_APPLICATION = 'attendance_system_facial_recognition.wsgi.application'

# ============================================================
# Database
# ============================================================
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': os.path.join(BASE_DIR, 'db.sqlite3'),
        'CONN_MAX_AGE': 60,
    }
}

# ============================================================
# Password validation
# ============================================================
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# ============================================================
# Internationalization
# ============================================================
LANGUAGE_CODE = 'fa-ir'
TIME_ZONE = 'Asia/Tehran'
USE_I18N = True
USE_L10N = True
USE_TZ = True

# ============================================================
# Static / Media
# ============================================================
STATIC_URL = '/static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')

MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')

# ============================================================
# عکس‌های لحظهٔ ورود/خروج (بازبینی دستی تشخیص چهره)
# ============================================================
# حداکثر تعداد عکس نگهداری‌شده برای هر کاربر؛ با پر شدن، قدیمی‌ترین حذف می‌شود
# و عکس جدید جای آن می‌نشیند. از طریق متغیر محیط SNAPSHOT_LIMIT_PER_USER هم
# قابل تنظیم است.
SNAPSHOT_LIMIT_PER_USER = int(os.environ.get('SNAPSHOT_LIMIT_PER_USER', '50'))

STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'whitenoise.storage.CompressedStaticFilesStorage'},
}

WHITENOISE_MAX_AGE = 31536000
WHITENOISE_USE_FINDERS = True
WHITENOISE_STATIC_PREFIX = '/static/'
WHITENOISE_ROOT = STATIC_ROOT
WHITENOISE_COMPRESS_EXTENSIONS = ['css', 'js', 'svg', 'woff', 'woff2', 'ttf', 'eot', 'otf']
WHITENOISE_SKIP_COMPRESS_EXTENSIONS = ['jpg', 'jpeg', 'png', 'gif', 'webp', 'avif', 'ico', 'gz', 'br']
WHITENOISE_MIMETYPES = {
    '.woff': 'font/woff',
    '.woff2': 'font/woff2',
    '.ttf': 'font/ttf',
    '.eot': 'application/vnd.ms-fontobject',
    '.otf': 'font/otf',
}

# ============================================================
# Crispy forms
# ============================================================
CRISPY_ALLOWED_TEMPLATE_PACKS = "bootstrap4"
CRISPY_TEMPLATE_PACK = 'bootstrap4'

# ============================================================
# Jazzmin — ظاهر پنل مدیریت جنگو
# ============================================================
JAZZMIN_SETTINGS = {
    'site_title': 'سامانه حضور و غیاب',
    'site_header': 'پنل مدیریت حضور و غیاب',
    'site_brand': '<i class="fas fa-user-check"></i> حضور و غیاب',
    'site_logo': 'recognition/img/logo-1.png',
    'site_logo_classes': 'img-circle',
    'site_icon': None,
    'welcome_sign': 'به پنل مدیریت سامانه حضور و غیاب خوش آمدید',
    'copyright': 'شرکت مهندسی دانش‌بنیان کیان پرداز نقش جهان',
    'search_model': ['users.Time', 'auth.User', 'users.CheckOutError'],
    'show_sidebar': True,
    'navigation_expanded': True,
    'hide_apps': ['recognition'],
    'hide_models': [],
    'order_with_respect_to': ['auth', 'users'],
    'custom_links': {
        'users': [
            {
                'name': 'لیست کارمندان (سایت)',
                'url': '/users/',
                'icon': 'fas fa-users',
                'permissions': ['users.view_user'],
            },
            {
                'name': 'تغییر نام کاربری (بدون آسیب به مدل چهره)',
                'url': '/admin/tools/rename-users/',
                'icon': 'fas fa-user-edit',
                'permissions': ['auth.change_user'],
            },
            {
                'name': 'دلایل خروج',
                'url': '/manage/exit-reasons/',
                'icon': 'fas fa-door-open',
                'permissions': ['users.view_exitreason'],
            },
            {
                'name': 'خطاهای خروج',
                'url': '/checkout-errors/',
                'icon': 'fas fa-exclamation-triangle',
                'permissions': ['users.view_checkouterror'],
            },
        ],
    },
    'icons': {
        'auth.Group': 'fas fa-users-cog',
        'auth.User': 'fas fa-user',
        'users.Time': 'fas fa-clock',
        'users.Present': 'fas fa-check-circle',
        'users.Break': 'fas fa-coffee',
        'users.CheckOutError': 'fas fa-exclamation-triangle',
        'users.UserProfile': 'fas fa-id-badge',
        'users.ExitReason': 'fas fa-door-open',
    },
    'default_icon_parents': 'fas fa-folder',
    'default_icon_children': 'fas fa-circle',
    'related_modal_active': True,
    'show_ui_builder': False,
    'changeform_format': 'horizontal_tabs',
    'changeform_format_overrides': {
        'auth.user': 'collapsible',
        'users.time': 'collapsible',
    },
    'language_chooser': False,
    'use_google_fonts_cdn': False,
    'custom_css': '/static/recognition/css/jazzmin_admin.css',
}

JAZZMIN_UI_TWEAKS = {
    'navbar_small_text': False,
    'footer_small_text': False,
    'body_small_text': False,
    'brand_small_text': False,
    'brand_colour': 'navbar-indigo',
    'accent': 'accent-primary',
    'navbar': 'navbar-dark navbar-navy',
    'no_navbar_border': False,
    'navbar_fixed': True,
    'layout_boxed': False,
    'footer_fixed': False,
    'sidebar_fixed': True,
    'sidebar': 'sidebar-dark-primary',
    'sidebar_nav_small_text': False,
    'sidebar_disable_expand': False,
    'sidebar_nav_child_indent': True,
    'sidebar_nav_compact_style': False,
    'sidebar_nav_legacy_style': False,
    'sidebar_nav_flat_style': False,
    'theme': 'cyborg',
    'dark_mode_theme': None,
    'button_classes': {
        'primary': 'btn-primary',
        'secondary': 'btn-outline-secondary',
        'info': 'btn-info',
        'warning': 'btn-warning',
        'danger': 'btn-danger',
        'success': 'btn-success',
    },
    'custom_css': None,
    'custom_js': None,
}

# ============================================================
# Auth redirects
# ============================================================
LOGIN_URL = 'login'
LOGOUT_REDIRECT_URL = 'home'
LOGIN_REDIRECT_URL = 'dashboard'

# ============================================================
# Security — Maximum protection (configurable for dev)
# ============================================================
SECURE_SSL_REDIRECT = USE_HTTPS
SECURE_HSTS_SECONDS = 31536000 if USE_HTTPS else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = USE_HTTPS
SECURE_HSTS_PRELOAD = USE_HTTPS
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_BROWSER_XSS_FILTER = True
SECURE_REFERRER_POLICY = 'strict-origin-when-cross-origin'
SECURE_CROSS_ORIGIN_OPENER_POLICY = 'same-origin'
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https') if USE_HTTPS else None

X_FRAME_OPTIONS = 'DENY'

# Content Security Policy
SECURE_CONTENT_SECURITY_POLICY = {
    'default-src': "'self'",
    'style-src': "'self' 'unsafe-inline'",
    'script-src': "'self'",
    'font-src': "'self' data:",
    'img-src': "'self' data:",
    'connect-src': "'self'",
    'frame-ancestors': "'none'",
    'form-action': "'self'",
    'base-uri': "'self'",
    'object-src': "'none'",
}

# ============================================================
# Additional security headers
# ============================================================
CSP_INCLUDE_NONCE_IN = ['script-src', 'style-src']
CSP_EXCLUDE_URL_PREFIXES = ['/admin/']

# File upload security
FILE_UPLOAD_MAX_MEMORY_SIZE = 5242880  # 5MB
DATA_UPLOAD_MAX_MEMORY_SIZE = 5242880
FILE_UPLOAD_PERMISSIONS = 0o640

# ============================================================
# Logging
# ============================================================
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {process:d} {thread:d} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'file': {
            'level': 'WARNING',
            'class': 'logging.FileHandler',
            'filename': os.path.join(BASE_DIR, 'django.log'),
            'formatter': 'verbose',
        },
        'console': {
            'level': 'INFO',
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
    },
    'root': {
        'handlers': ['console', 'file'],
        'level': 'WARNING',
    },
    'loggers': {
        'django': {
            'handlers': ['console', 'file'],
            'level': 'WARNING',
            'propagate': False,
        },
        'django.security': {
            'handlers': ['console', 'file'],
            'level': 'WARNING',
            'propagate': False,
        },
    },
}

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'