from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import User, Group
from django.utils.html import format_html
from django.urls import reverse
from .models import Time, Present, Break, CheckOutError, UserProfile, ExitReason, AttendanceSnapshot
from .admin_tools import get_urls as _admin_tools_urls  # noqa: F401  (نویسنده: ابزار تغییر نام)

# ============================================================
# کاربران (User) + پروفایل نقش
# ============================================================
class UserProfileInline(admin.StackedInline):
    model = UserProfile
    can_delete = False
    verbose_name_plural = 'نقش کاربر'
    # شناسهٔ دائمی فقط‌خواندنی است: عوض‌کردنش یعنی از دست‌رفتن عکس‌های آن کاربر
    readonly_fields = ('face_uid_display',)
    fields = ('role', 'face_uid_display')

    @admin.display(description='شناسهٔ دائمی چهره')
    def face_uid_display(self, obj):
        return obj.face_uid or '—'


admin.site.unregister(User)


class KianAdminSite(admin.AdminSite):
    """سایت ادمین با یک لینک اضافه در بخش «ابزارها»."""

    site_header = 'سامانهٔ حضور و غیاب'
    site_title = 'پنل مدیریت'
    index_title = 'خوش آمدید'

    def each_context(self, request):
        ctx = super().each_context(request)
        ctx['kian_tools'] = [{
            'title': 'تغییر نام کاربری (بدون آسیب به مدل چهره)',
            'url': reverse('admin:user-tools-rename'),
        }]
        return ctx

    def get_urls(self):
        from .admin_tools import get_urls as tools_urls
        return tools_urls() + super().get_urls()


admin.site.__class__ = KianAdminSite


@admin.register(User)
class AppUserAdmin(UserAdmin):
    list_display = (
        'username', 'full_name', 'role_badge', 'photo_count_badge',
        'is_staff', 'is_superuser', 'last_login', 'date_joined',
    )
    list_filter = ('is_staff', 'is_superuser', 'is_active', 'profile__role')
    search_fields = ('username', 'first_name', 'last_name', 'email')
    ordering = ('-date_joined',)
    inlines = [UserProfileInline]

    @admin.display(description='نام کامل')
    def full_name(self, obj):
        return (('%s %s' % (obj.first_name, obj.last_name)).strip() or obj.username)

    @admin.display(description='عکس چهره')
    def photo_count_badge(self, obj):
        from .faceid import has_photos
        if has_photos(obj):
            return format_html(
                '<span style="color:#10b981;font-weight:600">&#8207;دارد</span>')
        return format_html(
            '<span style="color:#94a3b8">&#8207;ندارد</span>')

    @admin.display(description='نقش')
    def role_badge(self, obj):
        try:
            role = obj.profile.role
        except UserProfile.DoesNotExist:
            role = UserProfile.ROLE_USER if not obj.is_superuser else UserProfile.ROLE_ADMIN
        colors = {
            UserProfile.ROLE_USER: '#94a3b8',
            UserProfile.ROLE_ADMIN: '#8b5cf6',
            UserProfile.ROLE_BOTH: '#06b6d4',
        }
        return format_html(
            '<span style="background:{}25;color:{};padding:3px 10px;'
            'border-radius:20px;font-weight:600">&#8207;{}</span>',
            colors.get(role, '#888'), colors.get(role, '#888'),
            dict(UserProfile.ROLE_CHOICES).get(role, role),
        )


# ============================================================
# زمان‌های ورود/خروج (Time)
# ============================================================
@admin.register(Time)
class TimeAdmin(admin.ModelAdmin):
    list_display = ('user', 'date', 'time', 'out_badge', 'exit_reason', 'exit_reason_text')
    list_filter = ('out', 'exit_reason', 'date')
    search_fields = (
        'user__username', 'user__first_name', 'user__last_name',
        'exit_reason_text', 'exit_reason__title',
    )
    autocomplete_fields = ('user', 'exit_reason')
    date_hierarchy = 'date'
    ordering = ('-date', '-time')
    list_per_page = 50

    @admin.display(description='نوع', boolean=True)
    def out_badge(self, obj):
        return obj.out  # True=خروج


# ============================================================
# حضور روزانه (Present)
# ============================================================
@admin.register(Present)
class PresentAdmin(admin.ModelAdmin):
    list_display = ('user', 'date', 'present_badge')
    list_filter = ('present', 'date')
    search_fields = ('user__username', 'user__first_name', 'user__last_name')
    autocomplete_fields = ('user',)
    date_hierarchy = 'date'
    ordering = ('-date', 'user__username')
    list_per_page = 50

    @admin.display(description='حاضر', boolean=True)
    def present_badge(self, obj):
        return obj.present


# ============================================================
# استراحت (Break)
# ============================================================
@admin.register(Break)
class BreakAdmin(admin.ModelAdmin):
    list_display = ('user', 'date', 'break_out', 'break_in', 'dur', 'reason')
    list_filter = ('date',)
    search_fields = ('user__username', 'user__first_name', 'user__last_name', 'reason')
    autocomplete_fields = ('user',)
    date_hierarchy = 'date'
    ordering = ('-break_out',)
    list_per_page = 50

    @admin.display(description='مدت')
    def dur(self, obj):
        d = obj.duration_display()
        return d if d and d != '0 دقیقه' else '—'


# ============================================================
# خطاهای خروج (CheckOutError)
# ============================================================
@admin.register(CheckOutError)
class CheckOutErrorAdmin(admin.ModelAdmin):
    list_display = (
        'user', 'date', 'status_badge', 'auto_out_time', 'time_record_link',
        'resolved_by', 'resolved_at',
    )
    list_filter = ('status', 'date')
    search_fields = (
        'user__username', 'user__first_name', 'user__last_name', 'note',
    )
    autocomplete_fields = ('user',)
    readonly_fields = (
        'user', 'date', 'time_record', 'auto_out_time', 'created_at',
        'resolved_by', 'resolved_at',
    )
    ordering = ('-date', '-created_at')
    actions = ['mark_resolved']

    @admin.display(description='وضعیت')
    def status_badge(self, obj):
        if obj.status == CheckOutError.STATUS_RESOLVED:
            return format_html('<span style="color:{}">&#8207;رفع‌شده</span>', '#10b981')
        return format_html('<span style="color:{}">&#8207;در انتظار بررسی</span>', '#ef4444')

    @admin.display(description='رکورد زمان')
    def time_record_link(self, obj):
        if obj.time_record_id:
            url = '/admin/users/time/%d/change/' % obj.time_record_id
            return format_html('<a href="{}">{}</a>', url, obj.auto_out_time.strftime('%H:%M'))
        return '—'

    @admin.action(description='علامت‌گذاری به‌عنوان رفع‌شده')
    def mark_resolved(self, request, queryset):
        from django.utils import timezone
        updated = queryset.filter(status=CheckOutError.STATUS_PENDING).update(
            status=CheckOutError.STATUS_RESOLVED,
            resolved_by=request.user,
            resolved_at=timezone.now(),
        )
        self.message_user(request, '%d خطا به‌عنوان رفع‌شده علامت خورد.' % updated)


# ============================================================
# پروفایل/نقش کاربر (UserProfile)
# ============================================================
@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'role', 'role_badge')
    list_filter = ('role',)
    search_fields = ('user__username', 'user__first_name', 'user__last_name')
    autocomplete_fields = ('user',)
    list_editable = ('role',)
    list_per_page = 50

    @admin.display(description='نقش')
    def role_badge(self, obj):
        colors = {
            UserProfile.ROLE_USER: '#94a3b8',
            UserProfile.ROLE_ADMIN: '#8b5cf6',
            UserProfile.ROLE_BOTH: '#06b6d4',
        }
        return format_html(
            '<span style="background:{}25;color:{};padding:3px 10px;'
            'border-radius:20px;font-weight:600">&#8207;{}</span>',
            colors.get(obj.role, '#888'), colors.get(obj.role, '#888'),
            obj.get_role_display(),
        )


# ============================================================
# عکس‌های لحظهٔ ورود/خروج (AttendanceSnapshot)
# ============================================================
@admin.register(AttendanceSnapshot)
class AttendanceSnapshotAdmin(admin.ModelAdmin):
    list_display = ('user', 'event_badge', 'created_at')
    list_filter = ('event', 'created_at', 'user__username')
    search_fields = ('user__username', 'user__first_name', 'user__last_name')
    autocomplete_fields = ('user',)
    date_hierarchy = 'created_at'
    ordering = ('-created_at',)
    readonly_fields = ('user', 'event', 'photo', 'created_at')
    list_per_page = 50

    @admin.display(description='رویداد')
    def event_badge(self, obj):
        if obj.event == AttendanceSnapshot.EVENT_IN:
            return format_html('<span style="color:{}">&#8207;ورود</span>', '#10b981')
        return format_html('<span style="color:{}">&#8207;خروج</span>', '#ef4444')


# ============================================================
# دلایل خروج (ExitReason)
# ============================================================
@admin.register(ExitReason)
class ExitReasonAdmin(admin.ModelAdmin):
    list_display = ('title', 'sort_order', 'is_active_badge', 'is_system_badge')
    list_editable = ('sort_order',)
    list_filter = ('is_active', 'is_system')
    search_fields = ('title',)
    ordering = ('sort_order', 'title')

    @admin.display(description='فعال', boolean=True)
    def is_active_badge(self, obj):
        return obj.is_active

    @admin.display(description='سیستمی', boolean=True)
    def is_system_badge(self, obj):
        return obj.is_system