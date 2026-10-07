from django.contrib import admin

from .models import Errand


@admin.register(Errand)
class ErrandAdmin(admin.ModelAdmin):
    list_display = [
        'user', 'start_local', 'end_local', 'duration_display',
        'status_badge', 'reason_short', 'reviewed_by', 'reviewed_at',
    ]
    list_filter = ['status', 'start_time']
    search_fields = ['user__username', 'user__first_name', 'user__last_name', 'reason']
    date_hierarchy = 'start_time'
    ordering = ['-start_time']
    list_select_related = ['user', 'reviewed_by']
    readonly_fields = ['created_at', 'updated_at']

    fieldsets = [
        ('مأموریت', {
            'fields': ('user', 'start_time', 'end_time', 'reason', 'destination', 'contact'),
        }),
        ('وضعیت', {
            'fields': ('status', 'reviewed_by', 'reviewed_at', 'review_note'),
        }),
        ('تاریخچه', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    ]

    @admin.display(description='شروع (تهران)', ordering='start_time')
    def start_local(self, obj):
        from django.utils import timezone
        return timezone.localtime(obj.start_time).strftime('%Y-%m-%d %H:%M')

    @admin.display(description='پایان (تهران)', ordering='end_time')
    def end_local(self, obj):
        from django.utils import timezone
        return timezone.localtime(obj.end_time).strftime('%m-%d %H:%M')

    @admin.display(description='مدت', ordering='duration_seconds')
    def duration_display(self, obj):
        return obj.duration_display()

    @admin.display(description='وضعیت', ordering='status')
    def status_badge(self, obj):
        return obj.get_status_display()

    @admin.display(description='دلیل')
    def reason_short(self, obj):
        text = obj.reason or '—'
        return text if len(text) <= 40 else text[:40] + '…'

    @admin.display(description='ساعت کار (با احتساب معموریت)')
    def work_seconds_with_errand(self, obj):
        """فقط اگر تأییدشده باشد، بازهٔ آن روز را نشان بدهد."""
        if not obj.is_approved:
            return '—'
        return obj.duration_display()