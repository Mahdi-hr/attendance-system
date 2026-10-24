"""نمایش حساب‌های بله در پنل ادمین جنگو."""

from django.contrib import admin

from .models import BaleAccount, BotState


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
