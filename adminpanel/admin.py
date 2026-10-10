"""ثبت مدل‌های خودِ جنگو (گروه‌ها، لاگ اکشن‌ها، نشست‌ها) در پنل.

این مدل‌ها پیش‌تر یا اصلاً ثبت نشده بودند یا با پیکربندی پیش‌فرض جنگو
بودند؛ اینجا با همان سبک بقیهٔ پنل (ستون‌های خوانا، فیلتر، جستجو)
ثبت می‌شوند تا مدیر برای «چه کسی چه کردی» و «نشست‌های باز» نیازی به
ترمینال نداشته باشد.
"""
from django.contrib import admin
from django.contrib.admin.models import LogEntry
# import صریح تا ثبتِ پیش‌فرض جنگو روی Group زودتر از این فایل انجام شود؛
# وگرنه `admin.site.unregister` بالای همین فایل با `AlreadyRegistered`
# در زمان بارگذاری `django.contrib.auth.admin` مواجه می‌شدیم.
import django.contrib.auth.admin  # noqa: F401
from django.contrib.auth.models import Group
from django.contrib.sessions.models import Session
from django.utils import timezone
from django.utils.html import format_html


# ============================================================
# گروه‌ها و دسترسی‌ها
# ============================================================
class GroupAdmin(admin.ModelAdmin):
    list_display = ("name", "perm_count", "user_count")
    search_fields = ("name",)
    ordering = ("name",)
    list_per_page = 50
    show_full_result_count = False
    filter_horizontal = ("permissions",)

    @admin.display(description="دسترسی‌ها", ordering="permissions__count")
    def perm_count(self, obj):
        n = getattr(obj, "_perms", None)
        if n is None:
            n = obj.permissions.count()
        return format_html('<span class="badge bg-primary">{}</span>', n)

    @admin.display(description="اعضا", ordering="user__count")
    def user_count(self, obj):
        n = getattr(obj, "_users", None)
        if n is None:
            n = obj.user_set.count()
        return format_html('<span class="badge bg-secondary">{}</span>', n)

    def get_queryset(self, request):
        from django.db.models import Count
        return (super().get_queryset(request)
                .annotate(_perms=Count("permissions", distinct=True),
                          _users=Count("user", distinct=True)))


# ============================================================
# تاریخچهٔ اکشن‌های پنل ادمین
# ============================================================
class LogEntryAdmin(admin.ModelAdmin):
    list_display = ("when", "user_col", "action_col", "object_link", "ip")
    list_filter = ("action_flag", "content_type")
    search_fields = ("object_repr", "change_message", "user__username")
    ordering = ("-action_time",)
    date_hierarchy = "action_time"
    list_per_page = 50
    show_full_result_count = False
    readonly_fields = (
        "action_time", "user", "content_type", "object_id", "object_repr",
        "action_flag", "change_message", "ip",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @admin.display(description="زمان", ordering="action_time")
    def when(self, obj):
        return timezone.localtime(obj.action_time).strftime("%Y/%m/%d %H:%M:%S")

    @admin.display(description="کاربر", ordering="user__username")
    def user_col(self, obj):
        return obj.user.username if obj.user_id else '—'

    @admin.display(description="عملیات", ordering="action_flag")
    def action_col(self, obj):
        flag = obj.action_flag
        if flag == 1:
            return format_html('<span class="ap-pill ap-pill-ok">ایجاد</span>')
        if flag == 2:
            return format_html('<span class="ap-pill ap-pill-info">ویرایش</span>')
        return format_html('<span class="ap-pill ap-pill-error">حذف</span>')

    @admin.display(description="شیء")
    def object_link(self, obj):
        return format_html(
            '<span dir="ltr">{}</span>', obj.object_repr[:80])

    @admin.display(description="توضیحات")
    def ip(self, obj):
        """IP ثبت‌شده در message (در صورت وجود) یا خط توضیحات."""
        msg = obj.change_message or ""
        if "IP=" in msg:
            return format_html("<code dir='ltr'>{}</code>",
                               msg.split("IP=", 1)[1].split()[0])
        return format_html('<small class="text-muted">{}</small>', msg[:70])


# ============================================================
# نشست‌های فعال (Session)
# ============================================================
class SessionAdmin(admin.ModelAdmin):
    list_display = ("key_short", "user_col", "expires_local", "data_summary")
    search_fields = ("session_key",)
    ordering = ("-expire_date",)
    date_hierarchy = "expire_date"
    list_per_page = 50
    show_full_result_count = False
    readonly_fields = ("session_key", "expire_date", "session_data")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @admin.display(description="کلید")
    def key_short(self, obj):
        return format_html("<code dir='ltr'>{}…</code>",
                           obj.session_key[:12])

    @admin.display(description="کاربر")
    def user_col(self, obj):
        try:
            data = obj.get_decoded()
        except Exception:
            return format_html('<span class="ap-pill ap-pill-error">خراب</span>')
        uid = data.get("_auth_user_id")
        if not uid:
            return format_html('<span class="text-muted">مهمان</span>')
        from django.contrib.auth import get_user_model
        user = get_user_model().objects.filter(pk=uid).first()
        return user.username if user else "کاربر #%s" % uid

    @admin.display(description="انقضا", ordering="expire_date")
    def expires_local(self, obj):
        return timezone.localtime(obj.expire_date).strftime("%Y/%m/%d %H:%M")

    @admin.display(description="خلاصه")
    def data_summary(self, obj):
        try:
            data = obj.get_decoded()
        except Exception:
            return format_html('<span class="ap-pill ap-pill-error">داده خراب</span>')
        keys = ", ".join(sorted(k for k in data.keys() if k != "session_key"))
        return format_html('<small dir="ltr">{}</small>', keys[:90] or "—")


# ============================================================
# ثبت نهایی (جایگزینی ثبت پیش‌فرض جنگو روی Group)
# ============================================================
def _swap(model, model_admin):
    """مدل را با `model_admin` سفارشی ثبت می‌کند (بدون خطای تکراری)."""
    if model in admin.site._registry:
        admin.site.unregister(model)
    admin.site.register(model, admin_class=model_admin)


_swap(Group, GroupAdmin)
admin.site.register(LogEntry, LogEntryAdmin)
admin.site.register(Session, SessionAdmin)
