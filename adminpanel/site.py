"""سایت ادمین سفارشی سامانهٔ حضور و غیاب.

سه کار اصلی این ماژول:

1. **فقط superuser** می‌تواند وارد /admin/ شود (کاربران عادی و حتی نقش
   «ادمینِ» سایت به پنل جنگو دسترسی ندارند).
2. داشبورد /admin/ با آمار زنده، صف‌های کاری و ابزارها جایگزین صفحهٔ
   پیش‌فرض جازمین می‌شود.
3. فهرست «ابزارها» را به سایدبار و هر صفحهٔ ادمین تزریق می‌کند.
"""
from django.contrib import admin
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone


# ============================================================
# فهرست ابزارها (در سایدبار و داشبورد نمایش داده می‌شود)
# ============================================================
def tool_menu():
    """گروه‌های ابزار؛ `url` با reverse ساخته می‌شود چون نام‌ها namepsace دارند."""

    def r(name):
        try:
            return reverse('admin:%s' % name)
        except Exception:  # URLconf هنوز بارگذاری نشده (موقع migrate و غیره)
            return '#'

    return [
        {
            'title': 'عملیات روزمره',
            'items': [
                {'name': 'داشبورد و آمار', 'url': r('index'),
                 'icon': 'fas fa-chart-pie'},
                {'name': 'لیست کارمندان (سایت)', 'url': '/users/',
                 'icon': 'fas fa-users'},
                {'name': 'دلایل خروج', 'url': '/manage/exit-reasons/',
                 'icon': 'fas fa-door-open'},
                {'name': 'خطاهای خروج', 'url': '/checkout-errors/',
                 'icon': 'fas fa-exclamation-triangle'},
                {'name': 'مأموریت‌ها', 'url': '/manage/errands/',
                 'icon': 'fas fa-route'},
                {'name': 'عکس‌های لحظه ورود/خروج', 'url': '/manage/snapshots/',
                 'icon': 'fas fa-camera'},
            ],
        },
        {
            'title': 'ابزارهای مدیریت',
            'items': [
                {'name': 'سلامت و تعمیر سیستم', 'url': r('adminpanel-health'),
                 'icon': 'fas fa-heartbeat'},
                {'name': 'ایجاد گروهی کاربران', 'url': r('adminpanel-users-create'),
                 'icon': 'fas fa-user-plus'},
                {'name': 'عملیات گروهی کاربران', 'url': r('adminpanel-users-bulk'),
                 'icon': 'fas fa-users-cog'},
                {'name': 'ترمیم حضور و غیاب', 'url': r('adminpanel-attendance'),
                 'icon': 'fas fa-calendar-check'},
                {'name': 'پشتیبان‌گیری و بازیابی دیتابیس', 'url': r('adminpanel-backup'),
                 'icon': 'fas fa-database'},
                {'name': 'کاوشگر دیتابیس (SQL)', 'url': r('adminpanel-sql'),
                 'icon': 'fas fa-terminal'},
                {'name': 'لاگ سیستم', 'url': r('adminpanel-logs'),
                 'icon': 'fas fa-file-alt'},
                {'name': 'پاک‌سازی عکس‌های چهره', 'url': r('adminpanel-snapshots'),
                 'icon': 'fas fa-broom'},
                {'name': 'مدیریت نشست‌ها', 'url': r('adminpanel-sessions'),
                 'icon': 'fas fa-fingerprint'},
                {'name': 'تغییر نام کاربری', 'url': r('adminpanel-rename'),
                 'icon': 'fas fa-user-edit'},
            ],
        },
    ]


def _safe_index():
    """آیا `admin:index` برعکس‌پذیر است؟ (فقط برای مواقعی که URLconf بار نشده)"""
    try:
        reverse('admin:index')
        return True
    except Exception:
        return False


# ============================================================
# سایت ادمین
# ============================================================
class KianAdminSite(admin.AdminSite):
    """سایت ادمین اختصاصی سامانهٔ حضور و غیاب."""

    site_header = 'سامانهٔ حضور و غیاب'
    site_title = 'پنل مدیریت حضور و غیاب'
    index_title = 'داشبورد مدیریت'

    # --------------------------------------------------------
    # دسترسی — فقط superuser
    # --------------------------------------------------------
    def has_permission(self, request):
        """فقط ابرکاربر فعال می‌تواند وارد پنل جنگو شود.

        کاربران عادی و کاربرانی که در سایت نقش «ادمین» دارند ولی
        superuser نیستند، به /admin/ دسترسی ندارند و به صفحهٔ ورود
        هدایت می‌شوند.
        """
        user = request.user
        return bool(
            user.is_active and user.is_superuser and user.is_staff
        )

    # --------------------------------------------------------
    # زمینهٔ همهٔ صفحه‌ها
    # --------------------------------------------------------
    def each_context(self, request):
        ctx = super().each_context(request)
        ctx['adminpanel_menu'] = tool_menu()
        ctx['adminpanel_now'] = timezone.now()
        return ctx

    # --------------------------------------------------------
    # داشبورد
    # --------------------------------------------------------
    def index(self, request, extra_context=None):
        from .dashboard import build_dashboard
        context = {
            **self.each_context(request),
            'title': 'داشبورد مدیریت',
            'dashboard': build_dashboard(request),
            'app_list': self.get_app_list(request),
        }
        if extra_context:
            context.update(extra_context)
        request.current_app = self.name
        return TemplateResponse(request, 'admin/index.html', context)

    # --------------------------------------------------------
    # ابزارها (مسیرهای سفارشی زیر /admin/tools/)
    # --------------------------------------------------------
    def get_urls(self):
        from .views import tool_urls
        return tool_urls(self) + super().get_urls()


_patched = False


def patch_admin_site():
    """کلاس `admin.site` را با `KianAdminSite` عوض می‌کند (یک‌بار)."""
    global _patched
    if _patched:
        return
    admin.site.__class__ = KianAdminSite
    _patched = True
