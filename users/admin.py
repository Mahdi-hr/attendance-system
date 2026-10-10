"""ثبت‌نام مدل‌های اپ کاربران در پنل ادمین (بازنویسی‌شده).

تمرکز روی سه چیز:
  ۱. **سرعت** — `list_select_related`، `show_full_result_count=False`،
     بدون N+1 (شمارش عکس‌ها با یک اسکرین کش‌دار)، صفحه‌بندی سبک.
  ۲. **قدرت** — عملیات گروهی، خروجی اکسل، ترمیم داده، بدون نیاز به SQL.
  ۳. **خوانایی** — نشان‌های رنگی، تاریخ شمسی، فیلترهای هوشمند.
"""
from django.contrib import admin
from django.contrib import messages
from django.contrib.admin import helpers
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import User
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import render
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html

import datetime as dt

from .faceid import ensure_face_uid, ensure_profile
from .models import (
    AttendanceSnapshot,
    Break,
    CheckOutError,
    ExitReason,
    Present,
    Time,
    UserProfile,
)

from adminpanel.diagnostics import photo_count_for_user
from adminpanel.exports import xlsx_response
from adminpanel.utils import random_password

ROLE_COLORS = {
    UserProfile.ROLE_USER: '#94a3b8',
    UserProfile.ROLE_ADMIN: '#8b5cf6',
    UserProfile.ROLE_BOTH: '#06b6d4',
}


def _full_name(user):
    return (('%s %s' % (user.first_name, user.last_name)).strip()
            or user.username)


def _flush_sessions(user):
    """همهٔ نشست‌های یک کاربر را باطل می‌کند و تعداد را برمی‌گرداند."""
    from django.contrib.sessions.models import Session
    removed = 0
    for session in Session.objects.all():
        try:
            data = session.get_decoded()
        except Exception:
            continue
        if str(data.get('_auth_user_id')) == str(user.pk):
            session.delete()
            removed += 1
    return removed


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
    extra = 0
    max_num = 1

    @admin.display(description='شناسهٔ دائمی چهره')
    def face_uid_display(self, obj):
        if not obj or not obj.pk:
            return '— خودکار ساخته می‌شود'
        return format_html('<code dir="ltr">{}</code>', obj.face_uid or '—')


admin.site.unregister(User)


@admin.register(User)
class AppUserAdmin(UserAdmin):
    list_display = (
        'username', 'full_name', 'role_badge', 'photo_count_badge',
        'is_active_badge', 'is_staff', 'is_superuser',
        'last_login', 'date_joined',
    )
    list_filter = ('is_active', 'is_staff', 'is_superuser', 'profile__role',
                   'date_joined')
    search_fields = ('username', 'first_name', 'last_name', 'email',
                     'profile__face_uid')
    ordering = ('-date_joined',)
    date_hierarchy = 'date_joined'
    inlines = [UserProfileInline]
    list_select_related = ('profile',)
    list_per_page = 50
    show_full_result_count = False
    preserve_filters = True
    save_on_top = True
    actions_on_top = True
    actions_on_bottom = False
    radio_fields = {}
    filter_horizontal = ('groups', 'user_permissions')

    actions = [
        'act_goto_bulk', 'act_activate', 'act_deactivate',
        'act_reset_passwords', 'act_sync_profiles', 'act_force_logout',
        'act_grant_staff', 'act_revoke_staff', 'act_export',
    ]

    # ---------- ستون‌ها ----------

    @admin.display(description='نام کامل', ordering='last_name')
    def full_name(self, obj):
        return _full_name(obj)

    @admin.display(description='عکس چهره', ordering='profile__face_uid')
    def photo_count_badge(self, obj):
        # photo_count_for_user یک‌باره کل پوشهٔ آموزش را اسکن و کش می‌کند؛
        # برخلاف روش قبلی (یک listdir به‌ازای هر ردیف) N+1 نیست.
        n = photo_count_for_user(obj)
        if n:
            return format_html(
                '<span style="color:#10b981;font-weight:700">{} عکس</span>', n)
        return format_html(
            '<span style="color:#f59e0b;font-weight:700">ندارد</span>')

    @admin.display(description='نقش', ordering='profile__role')
    def role_badge(self, obj):
        try:
            role = obj.profile.role
        except UserProfile.DoesNotExist:
            role = (UserProfile.ROLE_ADMIN if obj.is_superuser
                    else UserProfile.ROLE_USER)
        color = ROLE_COLORS.get(role, '#888')
        label = dict(UserProfile.ROLE_CHOICES).get(role, role)
        if obj.is_superuser:
            label = 'superuser'
            color = '#ef4444'
        return format_html(
            '<span style="background:{}25;color:{};padding:3px 10px;'
            'border-radius:20px;font-weight:700">&#8207;{}</span>',
            color, color, label)

    @admin.display(description='فعال', boolean=True, ordering='is_active')
    def is_active_badge(self, obj):
        return obj.is_active

    # ---------- عملیات گروهی ----------

    @admin.action(description='➡️ رفتن به «عملیات گروهی پیشرفته»…')
    def act_goto_bulk(self, request, queryset):
        ids = ','.join(str(pk) for pk in queryset.values_list('pk', flat=True))
        url = reverse('admin:adminpanel-users-bulk') + '?ids=' + ids
        return HttpResponse(
            '<meta http-equiv="refresh" content="0;url=%s">'
            '<script>location.href=%r;</script>'
            '<p>در حال انتقال به صفحهٔ عملیات گروهی…</p>' % (url, url))

    @admin.action(description='✓ فعال‌سازی حساب‌های انتخاب‌شده')
    def act_activate(self, request, queryset):
        n = queryset.filter(is_active=False).update(is_active=True)
        self.message_user(request, '%s حساب فعال شد.' % n)

    @admin.action(description='✗ غیرفعال‌سازی حساب‌های انتخاب‌شده')
    def act_deactivate(self, request, queryset):
        qs = queryset.exclude(pk=request.user.pk)
        n = qs.filter(is_active=True).update(is_active=False)
        self.message_user(request, '%s حساب غیرفعال شد.' % n)

    @admin.action(description='🔑 ریست رمز عبور و نمایش رمزهای جدید')
    def act_reset_passwords(self, request, queryset):
        rows = []
        for user in queryset:
            password = random_password(12)
            user.set_password(password)
            user.save(update_fields=['password'])
            rows.append({'username': user.username, 'password': password})
        return render(request, 'admin/user_passwords.html', {
            **admin.site.each_context(request),
            'title': 'رمزهای جدید',
            'rows': rows,
        })

    @admin.action(description='🪪 همگام‌سازی پروفایل و شناسهٔ چهره')
    def act_sync_profiles(self, request, queryset):
        created = uid = 0
        for user in queryset:
            profile, was_created = UserProfile.objects.get_or_create(user=user)
            created += 1 if was_created else 0
            if not profile.face_uid:
                ensure_face_uid(user)
                uid += 1
        self.message_user(
            request,
            '%s پروفایل بررسی شد؛ %s پروفایل ساخته و %s شناسهٔ چهره تازه '
            'ساخته شد.' % (queryset.count(), created, uid))

    @admin.action(description='🚪 خروج اجباری از همهٔ دستگاه‌ها')
    def act_force_logout(self, request, queryset):
        total = sum(_flush_sessions(user) for user in queryset)
        self.message_user(
            request, '%s نشست باطل شد؛ کاربران انتخاب‌شده دوباره وارد می‌شوند.'
            % total)

    @admin.action(description='👮 دادن دسترسی پنل مدیریت (is_staff)')
    def act_grant_staff(self, request, queryset):
        n = queryset.filter(is_staff=False).update(is_staff=True)
        self.message_user(request, '%s کاربر دسترسی پنل گرفت.' % n)

    @admin.action(description='🚫 گرفتن دسترسی پنل مدیریت')
    def act_revoke_staff(self, request, queryset):
        n = queryset.exclude(pk=request.user.pk).filter(is_staff=True).update(
            is_staff=False)
        self.message_user(request, '%s کاربر دسترسی‌اش گرفته شد.' % n)

    @admin.action(description='⇩ خروجی اکسل از انتخاب‌شده‌ها')
    def act_export(self, request, queryset):
        rows = []
        for i, user in enumerate(queryset.select_related('profile'), start=1):
            try:
                role = user.profile.get_role_display()
                face_uid = user.profile.face_uid or '—'
            except UserProfile.DoesNotExist:
                role, face_uid = '—', '—'
            rows.append([
                i, user.username, user.first_name, user.last_name,
                user.email or '—', role, face_uid,
                photo_count_for_user(user),
                'فعال' if user.is_active else 'غیرفعال',
                'بله' if user.is_staff else 'خیر',
                'بله' if user.is_superuser else 'خیر',
                timezone.localtime(user.last_login).strftime('%Y/%m/%d %H:%M')
                if user.last_login else '—',
                timezone.localtime(user.date_joined).strftime('%Y/%m/%d %H:%M'),
            ])
        return xlsx_response(
            'users.xlsx', 'کاربران',
            headers=['ردیف', 'نام کاربری', 'نام', 'خانوادگی', 'ایمیل', 'نقش',
                     'شناسه چهره', 'تعداد عکس', 'وضعیت', 'دسترسی پنل',
                     'superuser', 'آخرین ورود', 'تاریخ عضویت'],
            rows=rows,
            widths=[7, 18, 16, 18, 26, 18, 18, 12, 12, 14, 12, 20, 20],
        )


# ============================================================
# زمان‌های ورود/خروج (Time)
# ============================================================
@admin.register(Time)
class TimeAdmin(admin.ModelAdmin):
    list_display = ('user', 'date', 'time_local', 'type_badge', 'exit_reason',
                    'exit_reason_text')
    list_filter = ('out', 'exit_reason', 'date')
    search_fields = (
        'user__username', 'user__first_name', 'user__last_name',
        'exit_reason_text', 'exit_reason__title',
    )
    autocomplete_fields = ('user', 'exit_reason')
    date_hierarchy = 'date'
    ordering = ('-date', '-time')
    list_per_page = 50
    list_select_related = ('user', 'exit_reason')
    show_full_result_count = False
    preserve_filters = True
    save_on_top = True
    actions = ['act_mark_in', 'act_mark_out', 'act_assign_reason',
               'act_export']
    list_editable = ()

    @admin.display(description='زمان', ordering='time')
    def time_local(self, obj):
        if not obj.time:
            return '—'
        return timezone.localtime(obj.time).strftime('%Y/%m/%d %H:%M')

    @admin.display(description='نوع', ordering='out')
    def type_badge(self, obj):
        if obj.out:
            return format_html(
                '<span class="ap-pill ap-pill-error">خروج</span>')
        return format_html('<span class="ap-pill ap-pill-ok">ورود</span>')

    @admin.action(description='→ تبدیل انتخاب‌شده‌ها به «ورود»')
    def act_mark_in(self, request, queryset):
        n = queryset.update(out=False)
        self.message_user(request, '%s رکورد «ورود» شد.' % n)

    @admin.action(description='← تبدیل انتخاب‌شده‌ها به «خروج»')
    def act_mark_out(self, request, queryset):
        n = queryset.update(out=True)
        self.message_user(request, '%s رکورد «خروج» شد.' % n)

    @admin.action(description='🏷️ انتخاب/تغییر دلیل خروج (صفحهٔ واسط)')
    def act_assign_reason(self, request, queryset):
        if request.POST.get('apply') == '1':
            reason_id = request.POST.get('reason')
            reason = (ExitReason.objects.filter(pk=reason_id).first()
                      if reason_id else None)
            ids = (request.POST.getlist('_selected_action')
                   or request.POST.getlist('ids'))
            updated = Time.objects.filter(pk__in=ids).update(
                exit_reason=reason)
            self.message_user(
                request,
                'دلیل خروج برای %s رکورد ثبت شد.' % updated,
                messages.SUCCESS)
            return HttpResponseRedirect(
                reverse('admin:users_time_changelist'))
        ids = [str(pk) for pk in queryset.values_list('pk', flat=True)]
        selected = Time.objects.filter(pk__in=ids).select_related(
            'user', 'exit_reason').order_by('-date', '-time')[:200]
        return render(request, 'admin/time_assign_reason.html', {
            **admin.site.each_context(request),
            'title': 'انتخاب دلیل خروج',
            'queryset': selected,
            'total': len(ids),
            'ids': ids,
            'reasons': ExitReason.objects.order_by('title'),
            'current_ids': set(),
            'opts': Time._meta,
            'action_checkbox_name': helpers.ACTION_CHECKBOX_NAME,
        })

    @admin.action(description='⇩ خروجی اکسل')
    def act_export(self, request, queryset):
        rows = []
        for i, t in enumerate(
                queryset.select_related('user', 'exit_reason')
                .order_by('date', 'time'), start=1):
            rows.append([
                i, t.user.username, _full_name(t.user),
                t.date.isoformat(),
                timezone.localtime(t.time).strftime('%Y/%m/%d %H:%M')
                if t.time else '—',
                'خروج' if t.out else 'ورود',
                t.exit_reason.title if t.exit_reason else '—',
                t.exit_reason_text or '—',
            ])
        return xlsx_response(
            'time-records.xlsx', 'ورود و خروج',
            headers=['ردیف', 'نام کاربری', 'نام کامل', 'تاریخ', 'زمان',
                     'نوع', 'دلیل خروج', 'متن دلیل'],
            rows=rows,
            widths=[7, 18, 22, 14, 20, 10, 24, 34],
        )


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
    list_select_related = ('user',)
    show_full_result_count = False
    preserve_filters = True
    actions = ['act_mark_present', 'act_mark_absent', 'act_rebuild',
               'act_export']

    @admin.display(description='حاضر', boolean=True, ordering='present')
    def present_badge(self, obj):
        return obj.present

    @admin.action(description='✓ علامت‌گذاری به‌عنوان حاضر')
    def act_mark_present(self, request, queryset):
        n = queryset.update(present=True)
        self.message_user(request, '%s ردیف «حاضر» شد.' % n)

    @admin.action(description='✗ علامت‌گذاری به‌عنوان غایب')
    def act_mark_absent(self, request, queryset):
        n = queryset.update(present=False)
        self.message_user(request, '%s ردیف «غایب» شد.' % n)

    @admin.action(description='🔄 بازسازی از روی رکوردهای ورود/خروج')
    def act_rebuild(self, request, queryset):
        pairs = set(
            Time.objects.filter(out=False, date__in=[p.date for p in queryset])
            .values_list('user_id', 'date').distinct()
        )
        changed = 0
        for present in queryset:
            should = (present.user_id, present.date) in pairs
            if present.present != should:
                present.present = should
                present.save(update_fields=['present'])
                changed += 1
        self.message_user(
            request, '%s از %s ردیف اصلاح شد.' % (changed, queryset.count()))

    @admin.action(description='⇩ خروجی اکسل')
    def act_export(self, request, queryset):
        rows = [[i, p.user.username, p.date.isoformat(),
                 'حاضر' if p.present else 'غایب']
                for i, p in enumerate(
                    queryset.select_related('user').order_by('-date'), 1)]
        return xlsx_response('present.xlsx', 'حضور روزانه',
                             headers=['ردیف', 'نام کاربری', 'تاریخ', 'وضعیت'],
                             rows=rows, widths=[7, 20, 14, 12])


# ============================================================
# استراحت (Break)
# ============================================================
@admin.register(Break)
class BreakAdmin(admin.ModelAdmin):
    list_display = ('user', 'date', 'break_out', 'break_in', 'dur',
                    'open_badge', 'reason')
    list_filter = ('date',)
    search_fields = ('user__username', 'user__first_name', 'user__last_name',
                     'reason')
    autocomplete_fields = ('user',)
    date_hierarchy = 'date'
    ordering = ('-break_out',)
    list_per_page = 50
    list_select_related = ('user',)
    show_full_result_count = False
    preserve_filters = True
    actions = ['act_close_open', 'act_delete_open', 'act_export']

    @admin.display(description='مدت')
    def dur(self, obj):
        d = obj.duration_display()
        return d if d and d != '0 دقیقه' else '—'

    @admin.display(description='وضعیت')
    def open_badge(self, obj):
        if obj.break_out and not obj.break_in:
            return format_html('<span class="ap-pill ap-pill-error">باز</span>')
        if obj.break_out and obj.break_in:
            return format_html('<span class="ap-pill ap-pill-ok">بسته</span>')
        return '—'

    @admin.action(description='🔒 بستن استراحت‌های بازِ انتخاب‌شده')
    def act_close_open(self, request, queryset):
        closed = 0
        for br in queryset.filter(break_in__isnull=True):
            br.break_in = br.break_out + dt.timedelta(minutes=30)
            if br.break_in > timezone.now():
                br.break_in = timezone.now()
            br.save(update_fields=['break_in'])
            closed += 1
        self.message_user(
            request,
            '%s استراحت بسته شد (مدت پیش‌فرض ۳۰ دقیقه؛ برای تغییر، هر ردیف '
            'را جدا ویرایش کنید).' % closed)

    @admin.action(description='🗑 حذف استراحت‌های بازِ بدون پایان')
    def act_delete_open(self, request, queryset):
        n = queryset.filter(break_in__isnull=True).delete()[0]
        self.message_user(request, '%s ردیف حذف شد.' % n)

    @admin.action(description='⇩ خروجی اکسل')
    def act_export(self, request, queryset):
        rows = []
        for i, b in enumerate(
                queryset.select_related('user').order_by('-date'), start=1):
            rows.append([
                i, b.user.username, b.date.isoformat(),
                timezone.localtime(b.break_out).strftime('%H:%M')
                if b.break_out else '—',
                timezone.localtime(b.break_in).strftime('%H:%M')
                if b.break_in else '—',
                b.duration_display(), b.reason or '—',
            ])
        return xlsx_response('breaks.xlsx', 'استراحت‌ها',
                             headers=['ردیف', 'نام کاربری', 'تاریخ', 'شروع',
                                      'پایان', 'مدت', 'دلیل'],
                             rows=rows, widths=[7, 18, 14, 10, 10, 18, 30])


# ============================================================
# خطاهای خروج (CheckOutError)
# ============================================================
@admin.register(CheckOutError)
class CheckOutErrorAdmin(admin.ModelAdmin):
    list_display = (
        'user', 'date', 'status_badge', 'auto_out_time', 'time_record_link',
        'note', 'resolved_by', 'resolved_at',
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
    list_select_related = ('user', 'resolved_by', 'time_record')
    list_per_page = 50
    show_full_result_count = False
    preserve_filters = True
    actions = ['mark_resolved', 'act_reopen', 'act_export']

    @admin.display(description='وضعیت', ordering='status')
    def status_badge(self, obj):
        if obj.status == CheckOutError.STATUS_RESOLVED:
            return format_html('<span class="ap-pill ap-pill-ok">رفع‌شده</span>')
        return format_html('<span class="ap-pill ap-pill-error">در انتظار</span>')

    @admin.display(description='رکورد زمان')
    def time_record_link(self, obj):
        if obj.time_record_id:
            url = '/admin/users/time/%d/change/' % obj.time_record_id
            return format_html('<a href="{}">{}</a>', url,
                               timezone.localtime(obj.auto_out_time).strftime('%H:%M'))
        return '—'

    @admin.action(description='✓ علامت‌گذاری به‌عنوان رفع‌شده')
    def mark_resolved(self, request, queryset):
        updated = queryset.filter(status=CheckOutError.STATUS_PENDING).update(
            status=CheckOutError.STATUS_RESOLVED,
            resolved_by=request.user,
            resolved_at=timezone.now(),
        )
        self.message_user(request, '%d خطا به‌عنوان رفع‌شده علامت خورد.' % updated)

    @admin.action(description='↩ بازگشت به «در انتظار بررسی»')
    def act_reopen(self, request, queryset):
        updated = queryset.filter(status=CheckOutError.STATUS_RESOLVED).update(
            status=CheckOutError.STATUS_PENDING,
            resolved_by=None,
            resolved_at=None,
        )
        self.message_user(request, '%d خطا دوباره باز شد.' % updated)

    @admin.action(description='⇩ خروجی اکسل')
    def act_export(self, request, queryset):
        rows = []
        for i, e in enumerate(
                queryset.select_related('user', 'resolved_by')
                .order_by('-date'), start=1):
            rows.append([
                i, e.user.username, e.date.isoformat(),
                timezone.localtime(e.auto_out_time).strftime('%Y/%m/%d %H:%M'),
                e.get_status_display(), e.note or '—',
                e.resolved_by.username if e.resolved_by_id else '—',
                timezone.localtime(e.resolved_at).strftime('%Y/%m/%d %H:%M')
                if e.resolved_at else '—',
            ])
        return xlsx_response('checkout-errors.xlsx', 'خطاهای خروج',
                             headers=['ردیف', 'نام کاربری', 'تاریخ',
                                      'زمان خودکار', 'وضعیت', 'یادداشت',
                                      'رفع‌کننده', 'زمان رفع'],
                             rows=rows,
                             widths=[7, 18, 14, 20, 16, 34, 18, 20])


# ============================================================
# پروفایل/نقش کاربر (UserProfile)
# ============================================================
@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'role', 'role_badge', 'face_uid_col',
                    'has_photo_col')
    list_filter = ('role',)
    search_fields = ('user__username', 'user__first_name', 'user__last_name',
                     'face_uid')
    autocomplete_fields = ('user',)
    list_editable = ('role',)
    list_per_page = 50
    list_select_related = ('user',)
    show_full_result_count = False
    readonly_fields = ('face_uid_hint',)
    fields = ('user', 'role', 'face_uid_hint')

    @admin.display(description='نقش', ordering='role')
    def role_badge(self, obj):
        color = ROLE_COLORS.get(obj.role, '#888')
        return format_html(
            '<span style="background:{}25;color:{};padding:3px 10px;'
            'border-radius:20px;font-weight:700">&#8207;{}</span>',
            color, color, obj.get_role_display())

    @admin.display(description='شناسهٔ چهره', ordering='face_uid')
    def face_uid_col(self, obj):
        return format_html('<code dir="ltr">{}</code>', obj.face_uid or '—')

    @admin.display(description='عکس چهره')
    def has_photo_col(self, obj):
        n = photo_count_for_user(obj.user)
        if n:
            return format_html('<span style="color:#10b981">{} عکس</span>', n)
        return format_html('<span style="color:#f59e0b">ندارد</span>')

    @admin.display(description='شناسهٔ چهره')
    def face_uid_hint(self, obj):
        if obj and obj.pk:
            return format_html(
                '<code dir="ltr">{}</code><br><small class="text-muted">'
                'شناسهٔ دائمی است؛ عوض‌کردنش باعث از دست رفتن عکس‌های این '
                'کاربر می‌شود و فقط‌خواندنی است.</small>',
                obj.face_uid or '—')
        return ('پس از ذخیره به‌صورت خودکار ساخته می‌شود؛ عوض‌کردنش باعث '
                'از دست رفتن عکس‌های کاربر می‌شود.')

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if not obj.face_uid:
            ensure_profile(obj.user)
            ensure_face_uid(obj.user)


# ============================================================
# عکس‌های لحظهٔ ورود/خروج (AttendanceSnapshot)
# ============================================================
@admin.register(AttendanceSnapshot)
class AttendanceSnapshotAdmin(admin.ModelAdmin):
    list_display = ('thumb', 'user', 'event_badge', 'photo_path',
                    'created_at')
    list_filter = ('event', 'created_at')
    search_fields = ('user__username', 'user__first_name', 'user__last_name',
                     'photo')
    autocomplete_fields = ('user',)
    date_hierarchy = 'created_at'
    ordering = ('-created_at', '-id')
    readonly_fields = ('user', 'event', 'photo', 'created_at', 'thumb_large')
    list_per_page = 40
    list_select_related = ('user',)
    show_full_result_count = False
    preserve_filters = True
    actions = ['act_delete_with_files']

    @admin.display(description='عکس')
    def thumb(self, obj):
        url = '/manage/snapshots/image/%d/' % obj.pk
        return format_html(
            '<img src="{}" loading="lazy" width="44" height="44" '
            'style="object-fit:cover;border-radius:8px;'
            'box-shadow:0 0 0 1px rgba(148,163,184,.3)" alt="">', url)

    @admin.display(description='بزرگ')
    def thumb_large(self, obj):
        url = '/manage/snapshots/image/%d/' % obj.pk
        return format_html(
            '<a href="{}" target="_blank" rel="noopener">'
            '<img src="{}" style="max-width:260px;border-radius:10px" '
            'alt=""></a>', url, url)

    @admin.display(description='رویداد', ordering='event')
    def event_badge(self, obj):
        if obj.event == AttendanceSnapshot.EVENT_IN:
            return format_html('<span class="ap-pill ap-pill-ok">ورود</span>')
        return format_html('<span class="ap-pill ap-pill-error">خروج</span>')

    @admin.display(description='فایل')
    def photo_path(self, obj):
        return format_html('<code dir="ltr" class="small">{}</code>', obj.photo)

    @admin.action(description='🗑 حذف انتخاب‌شده‌ها به‌همراه فایل‌ها')
    def act_delete_with_files(self, request, queryset):
        from .snapshots import abs_path_for
        import os
        n = 0
        for snap in queryset:
            path = abs_path_for(snap)
            if path:
                try:
                    os.remove(path)
                except OSError:
                    pass
            snap.delete()
            n += 1
        self.message_user(request, '%s عکس (رکورد و فایل) حذف شد.' % n)

    def delete_queryset(self, request, queryset):
        """حذف گروهی از پنل (دکمهٔ استاندارد) هم فایل را پاک کند."""
        from .snapshots import abs_path_for
        import os
        for snap in queryset:
            path = abs_path_for(snap)
            if path:
                try:
                    os.remove(path)
                except OSError:
                    pass
        queryset.delete()

    def delete_model(self, request, obj):
        from .snapshots import abs_path_for
        import os
        path = abs_path_for(obj)
        if path:
            try:
                os.remove(path)
            except OSError:
                pass
        obj.delete()


# ============================================================
# دلایل خروج (ExitReason)
# ============================================================
@admin.register(ExitReason)
class ExitReasonAdmin(admin.ModelAdmin):
    list_display = ('title', 'sort_order', 'is_active', 'is_system',
                    'used_count')
    list_editable = ('sort_order', 'is_active')
    list_filter = ('is_active', 'is_system')
    search_fields = ('title',)
    ordering = ('sort_order', 'title')
    list_per_page = 50
    list_select_related = ()
    show_full_result_count = False
    actions = ['act_activate', 'act_deactivate']
    readonly_fields = ('used_count',)

    @admin.display(description='دفعات استفاده')
    def used_count(self, obj):
        annotated = getattr(obj, '_used', None)
        if annotated is not None:
            return annotated
        return Time.objects.filter(exit_reason=obj).count()

    def get_queryset(self, request):
        from django.db.models import Count
        return super().get_queryset(request).annotate(_used=Count('time'))

    @admin.action(description='✓ فعال‌سازی انتخاب‌شده‌ها')
    def act_activate(self, request, queryset):
        n = queryset.update(is_active=True)
        self.message_user(request, '%s دلیل فعال شد.' % n)

    @admin.action(description='✗ غیرفعال‌سازی انتخاب‌شده‌ها')
    def act_deactivate(self, request, queryset):
        n = queryset.update(is_active=False)
        self.message_user(request, '%s دلیل غیرفعال شد.' % n)
