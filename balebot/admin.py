"""نمایش حساب‌های بله در پنل ادمین جنگو."""

from django.contrib import admin

from .models import AuditLog, BaleAccount, BotConfig, BotState, ReminderLog


@admin.register(BaleAccount)
class BaleAccountAdmin(admin.ModelAdmin):
    list_display = (
        'bale_user_id', 'username', 'django_user', 'active_role',
        'is_blocked', 'linked_at', 'last_seen',
    )
    list_filter = ('active_role', 'is_blocked')
    search_fields = (
        'username', 'first_name', 'last_name',
        'django_user__username', 'bale_user_id',
    )
    readonly_fields = (
        'bale_user_id', 'chat_id', 'username', 'first_name', 'last_name',
        'created_at', 'linked_at', 'last_seen',
    )
    fieldsets = (
        ('حساب بله', {
            'fields': ('bale_user_id', 'chat_id', 'username',
                       'first_name', 'last_name', 'is_blocked'),
        }),
        ('اتصال به سامانه', {
            'fields': ('django_user', 'active_role', 'linked_at'),
        }),
        ('گفتگو', {
            'fields': ('state', 'state_data', 'failed_attempts', 'locked_until'),
            'classes': ('collapse',),
        }),
        ('زمان‌ها', {'fields': ('created_at', 'last_seen'), 'classes': ('collapse',)}),
    )


@admin.register(BotState)
class BotStateAdmin(admin.ModelAdmin):
    list_display = ('key', 'last_update_id', 'updated_at')
    readonly_fields = ('key', 'last_update_id', 'updated_at')


@admin.register(BotConfig)
class BotConfigAdmin(admin.ModelAdmin):
    list_display = (
        'singleton_id', 'bot_enabled', 'reminders_enabled',
        'work_end_time', 'pending_digest_time', 'upcoming_errand_minutes',
    )

    def has_add_permission(self, request):
        # فقط یک رکورد تنظیمات وجود دارد.
        return not BotConfig.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'actor', 'action', 'target_type', 'target_id')
    list_filter = ('action', 'target_type')
    search_fields = ('actor__username', 'action', 'details', 'target_id')
    date_hierarchy = 'created_at'
    readonly_fields = (
        'actor', 'action', 'target_type', 'target_id', 'details', 'created_at',
    )

    def has_add_permission(self, request):
        return False


@admin.register(ReminderLog)
class ReminderLogAdmin(admin.ModelAdmin):
    list_display = ('key', 'created_at')
    search_fields = ('key',)
    readonly_fields = ('key', 'created_at')

    def has_add_permission(self, request):
        return False
