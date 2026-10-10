from django.apps import AppConfig


class AdminPanelConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'adminpanel'
    verbose_name = 'ابزارهای مدیریت'

    def ready(self):
        """کلاس سایت ادمین را با نسخهٔ سفارشی عوض می‌کند.

        `django.contrib.admin.site` یک شیء واقعی است (نه lazy)، پس تغییر
        `__class__` آن بی‌خطر است و همهٔ ثبت‌نام‌های موجود (`@admin.register`)
        بدون هیچ تغییری روی همان سایت باقی می‌مانند.
        """
        from .site import patch_admin_site
        patch_admin_site()
