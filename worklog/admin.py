"""پنل ادمین جنگو برای پروژه‌ها و کارکردها."""

from django.contrib import admin

from .models import Project, WorkLog, WorkLogMonth


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ('name', 'sort_order', 'code', 'is_active', 'worklog_count')
    list_editable = ('sort_order', 'is_active')
    list_filter = ('is_active',)
    search_fields = ('name', 'code', 'description')
    ordering = ('sort_order', 'name')

    @admin.display(description='تعداد ثبت')
    def worklog_count(self, obj):
        return obj.worklogs.count()


@admin.register(WorkLog)
class WorkLogAdmin(admin.ModelAdmin):
    list_display = ('date', 'user', 'project', 'other_title', 'hours')
    list_filter = ('date', 'project')
    search_fields = (
        'user__username', 'user__first_name', 'user__last_name',
        'project__name', 'other_title',
    )
    date_hierarchy = 'date'
    autocomplete_fields = ('user', 'project')
    list_select_related = ('user', 'project')
    ordering = ('-date', 'user__username')


@admin.register(WorkLogMonth)
class WorkLogMonthAdmin(admin.ModelAdmin):
    list_display = ('user', 'year', 'month', 'status', 'submitted_at', 'updated_at')
    list_filter = ('status', 'year', 'month')
    search_fields = ('user__username', 'user__first_name', 'user__last_name')
    list_select_related = ('user',)
    ordering = ('-year', '-month', 'user__username')
