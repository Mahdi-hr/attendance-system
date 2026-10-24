"""ادمین جنگو برای «مأموریت‌ها» — پنل کامل مدیریت در /admin/errands/errand/.

امکانات:
  • فیلتر وضعیت/کارمند/بررسی‌کننده + جستجو + سلسله‌مراتب تاریخ
  • نمایش شمسیِ بازه، مدت و نشان رنگی وضعیت در ستون‌ها
  • عملیات گروهی: تأیید، رد با دلیل (صفحهٔ جداگانه)، بازگشت به
    «در انتظار»، لغو و خروجی اکسل از انتخاب‌شده‌ها
  • فرم ویرایش با فیلدهای فقط‌خواندنیِ محاسباتی و ثبت خودکار
    «بررسی‌کننده/زمان بررسی» هنگام تعیین تکلیف
"""

from django.contrib import admin, messages
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import render
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html

from recognition.excel_export import build_xlsx
from recognition.reports import clock, jalali_full
from recognition.views import XLSX_CONTENT_TYPE

from .models import Errand

# رنگ/آیکون هر وضعیت برای نشان‌های رنگی
STATUS_STYLES = {
    Errand.STATUS_PENDING: ('245, 158, 11', 'fa-hourglass-half'),
    Errand.STATUS_APPROVED: ('16, 185, 129', 'fa-check'),
    Errand.STATUS_REJECTED: ('239, 68, 68', 'fa-times'),
    Errand.STATUS_CANCELLED: ('148, 163, 184', 'fa-ban'),
}


def employee_label(user):
    """«نام خانوادگی نام (@username)» با ت fallback به نام کاربری."""
    full = ('%s %s' % (user.first_name, user.last_name)).strip()
    if full:
        return '%s (@%s)' % (full, user.username)
    return '@%s' % user.username


def jalali_range(errand):
    """بازهٔ شمسی «۱۴۰۵/۰۷/۱۵ — ۰۹:۳۰ تا ۱۷:۰۰»."""
    local = timezone.localtime(errand.start_time)
    return '%s — %s تا %s' % (
        jalali_full(local.date()),
        clock(errand.start_time),
        clock(errand.end_time),
    )


@admin.register(Errand)
class ErrandAdmin(admin.ModelAdmin):
    list_display = [
        'pk', 'employee', 'jalali_range_display', 'duration_display_col',
        'status_badge', 'reason_short', 'destination', 'reviewed_by_display',
        'reviewed_at_display', 'quick_review_link',
    ]
    list_filter = ['status', 'user', 'reviewed_by']
    search_fields = [
        'user__username', 'user__first_name', 'user__last_name',
        'reason', 'destination', 'contact', 'review_note',
    ]
    date_hierarchy = 'start_time'
    ordering = ['-start_time']
    list_select_related = ['user', 'reviewed_by']
    list_per_page = 25
    empty_value_display = '—'
    preserve_filters = True
    show_full_result_count = False
    save_on_top = True
    actions_on_top = True
    actions_on_bottom = False
    autocomplete_fields = ['user']
    readonly_fields = [
        'jalali_range_display', 'duration_display_col',
        'created_at', 'updated_at',
    ]

    fieldsets = [
        ('مأموریت', {
            'fields': (
                ('user', 'status'),
                ('start_time', 'end_time'),
                'reason',
                ('destination', 'contact'),
            ),
            'description': (
                'بازهٔ زمانی به وقت تهران ثبت می‌شود. مأموریت شبانه '
                '(عبور از نیمه‌شب) هم پشتیبانی می‌شود.'
            ),
        }),
        ('بررسی', {
            'fields': (('reviewed_by', 'reviewed_at'), 'review_note'),
            'description': (
                'در رد کردن، «یادداشت ادمین» الزامی است؛ کارمند آن را '
                'در صفحهٔ «مأموریت‌های من» می‌بیند.'
            ),
        }),
        ('محاسبات و تاریخچه', {
            'fields': (
                'jalali_range_display', 'duration_display_col',
                'created_at', 'updated_at',
            ),
            'classes': ('collapse',),
        }),
    ]

    actions = [
        'approve_selected', 'reject_selected', 'reset_selected',
        'cancel_selected', 'export_selected',
    ]

    # ---------- ستون‌های فهرست ----------

    @admin.display(description='کارمند', ordering='user__username')
    def employee(self, obj):
        return employee_label(obj.user)

    @admin.display(description='بازه (تهران)', ordering='start_time')
    def jalali_range_display(self, obj):
        # در صفحهٔ «افزودن» هنوز زمانی ثبت نشده است.
        if not obj.start_time or not obj.end_time:
            return '—'
        return jalali_range(obj)

    @admin.display(description='مدت', ordering='start_time')
    def duration_display_col(self, obj):
        # در صفحهٔ «افزودن» هنوز زمانی ثبت نشده است.
        if not obj.start_time or not obj.end_time:
            return '—'
        return obj.duration_display()

    @admin.display(description='وضعیت', ordering='status')
    def status_badge(self, obj):
        rgb, icon = STATUS_STYLES.get(obj.status, ('148, 163, 184', 'fa-circle'))
        return format_html(
            '<span style="display:inline-flex;align-items:center;gap:6px;'
            'padding:4px 11px;border-radius:12px;font-size:0.8rem;'
            'font-weight:700;white-space:nowrap;'
            'background:rgba({},0.14);color:rgb({});'
            'box-shadow:inset 0 0 0 1px rgba({},0.4);">'
            '<i class="fas {}"></i>{}</span>',
            rgb, rgb, rgb, icon, obj.get_status_display(),
        )

    @admin.display(description='دلیل', ordering='reason')
    def reason_short(self, obj):
        text = obj.reason or ''
        return text if len(text) <= 45 else text[:45] + '…'

    @admin.display(description='بررسی‌کننده', ordering='reviewed_by__username')
    def reviewed_by_display(self, obj):
        if not obj.reviewed_by_id:
            return '—'
        return ('%s %s' % (
            obj.reviewed_by.first_name, obj.reviewed_by.last_name,
        )).strip() or obj.reviewed_by.username

    @admin.display(description='زمان بررسی', ordering='reviewed_at')
    def reviewed_at_display(self, obj):
        if not obj.reviewed_at:
            return '—'
        local = timezone.localtime(obj.reviewed_at)
        return '%s — %s' % (jalali_full(local.date()), clock(obj.reviewed_at))

    @admin.display(description='عملیات سریع')
    def quick_review_link(self, obj):
        if not obj.is_pending:
            return '—'
        url = reverse('errands-admin') + '#errand-%d' % obj.pk
        return format_html(
            '<a class="button" href="{}" style="margin:0;'
            'padding:3px 10px;font-size:0.75rem;">بررسی در پنل</a>',
            url,
        )

    # ---------- عملیات گروهی ----------

    @admin.action(description='✓ تأیید درخواست‌های انتخاب‌شده')
    def approve_selected(self, request, queryset):
        done = skipped = 0
        for errand in queryset:
            if errand.approve(request.user):
                done += 1
            else:
                skipped += 1
        if done:
            messages.success(
                request,
                '%d مأموریت تأیید شد و از این پس در گزارش‌ها و خروجی‌ها '
                'محاسبه می‌شود.' % done,
            )
        if skipped:
            messages.warning(
                request,
                '%d مورد قبلاً بررسی شده بود و تغییر نکرد.' % skipped,
            )

    @admin.action(description='✗ رد گروهی با دلیل (صفحهٔ جداگانه)')
    def reject_selected(self, request, queryset):
        """به صفحهٔ میانی می‌رود تا دلیل ردِ الزامی گرفته شود."""
        ids = ','.join(
            str(pk) for pk in queryset.values_list('pk', flat=True)
        )
        return HttpResponseRedirect(
            reverse('admin:errands_errand_bulk_reject') + '?ids=' + ids,
        )

    @admin.action(description='↩ بازگشت به «در انتظار بررسی»')
    def reset_selected(self, request, queryset):
        updated = queryset.exclude(status=Errand.STATUS_PENDING).update(
            status=Errand.STATUS_PENDING,
            reviewed_by=None,
            reviewed_at=None,
            review_note='',
            updated_at=timezone.now(),
        )
        if updated:
            messages.success(
                request,
                '%d مأموریت به «در انتظار بررسی» برگشت. توجه: موارد '
                'تأییدشده تا بررسی دوباره روی گزارش‌ها اثر ندارند.' % updated,
            )
        else:
            messages.warning(request, 'همهٔ موارد انتخاب‌شده قبلاً در انتظار بودند.')

    @admin.action(description='🚫 لغو انتخاب‌شده‌ها')
    def cancel_selected(self, request, queryset):
        updated = queryset.exclude(status=Errand.STATUS_CANCELLED).update(
            status=Errand.STATUS_CANCELLED,
            reviewed_at=timezone.now(),
            updated_at=timezone.now(),
        )
        if updated:
            messages.success(request, '%d مأموریت لغو شد.' % updated)
        else:
            messages.warning(request, 'همهٔ موارد از قبل لغو شده بودند.')

    @admin.action(description='⇩ خروجی اکسل از انتخاب‌شده‌ها')
    def export_selected(self, request, queryset):
        rows = []
        for index, errand in enumerate(
            queryset.select_related('user', 'reviewed_by')
            .order_by('-start_time', 'user__username'),
            start=1,
        ):
            rows.append([
                str(index),
                jalali_range(errand),
                errand.duration_display(),
                employee_label(errand.user),
                errand.reason,
                errand.destination or '—',
                errand.get_status_display(),
                errand.review_note or '—',
                self.reviewed_by_display(errand),
            ])
        book = build_xlsx(
            sheet_name='مأموریت‌ها',
            headers=[
                'ردیف', 'بازه', 'مدت', 'کارمند', 'دلیل', 'مقصد',
                'وضعیت', 'یادداشت بررسی', 'بررسی‌کننده',
            ],
            rows=rows,
            widths=[8, 34, 16, 24, 40, 22, 16, 34, 20],
            rtl=True,
        )
        response = HttpResponse(book, content_type=XLSX_CONTENT_TYPE)
        response['Content-Disposition'] = (
            'attachment; filename="errands-selected.xlsx"'
        )
        return response

    # ---------- صفحهٔ میانی «رد گروهی» ----------

    def get_urls(self):
        custom = [
            path(
                'bulk-reject/',
                self.admin_site.admin_view(self.bulk_reject_view),
                name='errands_errand_bulk_reject',
            ),
        ]
        return custom + super().get_urls()

    def bulk_reject_view(self, request):
        """گرفتن دلیل رد (الزامی) و ردِ همهٔ درخواست‌های انتخاب‌شده."""
        ids_param = (
            request.POST.get('ids') or request.GET.get('ids') or ''
        )
        pks = [int(x) for x in ids_param.split(',') if x.strip().isdigit()]
        errands = list(
            Errand.objects.filter(pk__in=pks).select_related('user')
        )
        rows = [{
            'pk': e.pk,
            'employee': employee_label(e.user),
            'range': jalali_range(e),
            'reason': e.reason,
            'is_pending': e.is_pending,
            'status': e.get_status_display(),
        } for e in errands]

        if request.method == 'POST':
            note = (request.POST.get('note') or '').strip()
            if not note:
                messages.error(
                    request,
                    'دلیل رد الزامی است؛ کارمند باید ببیند چرا درخواستش رد شده.',
                )
            elif not errands:
                messages.warning(request, 'هیچ درخواستی برای رد پیدا نشد.')
                return HttpResponseRedirect(
                    reverse('admin:errands_errand_changelist')
                )
            else:
                done = skipped = 0
                for errand in errands:
                    if errand.reject(request.user, note=note):
                        done += 1
                    else:
                        skipped += 1
                if done:
                    messages.success(
                        request,
                        '%d مأموریت رد شد؛ دلیل برای کارمند نمایش داده '
                        'می‌شود.' % done,
                    )
                if skipped:
                    messages.warning(
                        request,
                        '%d مورد قبلاً بررسی شده بود و رد نشد.' % skipped,
                    )
                return HttpResponseRedirect(
                    reverse('admin:errands_errand_changelist')
                )

        context = dict(
            self.admin_site.each_context(request),
            title='رد گروهی مأموریت‌ها',
            rows=rows,
            count=len(rows),
            ids=ids_param,
            opts=self.model._meta,
        )
        return render(request, 'admin/errand_bulk_reject.html', context)

    # ---------- فرم تک‌موردی ----------

    def save_model(self, request, obj, form, change):
        """اگر وضعیت تعیین تکلیف شد ولی بررسی‌کننده ثبت نشده بود، خودکار ثبت شود."""
        if obj.status in (Errand.STATUS_APPROVED, Errand.STATUS_REJECTED):
            if not obj.reviewed_by_id:
                obj.reviewed_by = request.user
            if not obj.reviewed_at:
                obj.reviewed_at = timezone.now()
        super().save_model(request, obj, form, change)
