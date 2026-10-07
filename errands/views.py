"""ویوهای بخش معموریت.

سه گروه صفحه:
  ۱) کارمند  : ثبت درخواست، لیست «معموریت‌های من»، لغو، مشاهدهٔ نتیجه
  ۲) ادمین   : صف بررسی، تأیید/رد، ویرایش بازهٔ ثبت‌شده
  ۳) گزارش   : فهرست معموریت‌ها در یک بازه (برای خروجی اکسل و چاپ)

دسترسی با `can_access_admin(request)` کنترل می‌شود — همان الگوی
«پرچم admin_mode در session» که کل سامانه از آن استفاده می‌کند.
"""

import datetime as dt

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from recognition.excel_export import build_xlsx
from recognition.reports import fa_digits, format_hours, jalali_full, clock
from recognition.views import XLSX_CONTENT_TYPE, report_query_params
from users.roles import can_access_admin

from . import services
from .forms import ErrandAdminEditForm, ErrandFilterForm, ErrandForm, ErrandReviewForm
from .models import Errand

PER_PAGE = 25
MY_PER_PAGE = 3  # «معموریت‌های من»: حداکثر ۳ درخواست اخیر در هر صفحه از هر بخش


def _jalali_input(value):
    """value (datetime/date) → رشتهٔ میلادی برای پر کردن datepicker."""
    if not value:
        return ''
    local = timezone.localtime(value) if timezone.is_aware(value) else value
    return local.strftime('%Y-%m-%dT%H:%M')


def _filter_qs(request):
    """درخواست‌ها + فیلترهای GET را برمی‌گرداند.

    خروجی: (queryset, form, date_from, date_to)
    """
    form = ErrandFilterForm(request.GET)
    qs = Errand.objects.select_related('user', 'reviewed_by').all()
    date_from = date_to = None

    if form.is_valid():
        data = form.cleaned_data
        date_from = data.get('date_from')
        date_to = data.get('date_to')
        if data.get('status'):
            qs = qs.filter(status=data['status'])
        if data.get('q'):
            q = data['q'].strip()
            qs = qs.filter(
                Q(user__username__icontains=q)
                | Q(user__first_name__icontains=q)
                | Q(user__last_name__icontains=q)
            )
        if date_from or date_to:
            qs = qs.for_window(date_from, date_to)
    return qs, form, date_from, date_to


# ============================================================
#  ۱) کارمند
# ============================================================

@login_required
def my_errands(request):
    """ثبت درخواست جدید + فهرست معموریت‌های خودِ کارمند.

    اگر کاربر در «حالت ادمین» باشد، فرم ثبت نمایش داده نمی‌شود
    (ادمین‌ها فقط بررسی می‌کنند، درخواست ثبت نمی‌کنند).
    """
    from users.roles import can_access_admin
    user = request.user
    is_admin_mode = can_access_admin(request)

    # فقط در حالت غیر ادمین اجازه ثبت داده شود
    errand_form = None
    if not is_admin_mode:
        errand_form = ErrandForm(request.POST or None, user=user)

        if request.method == 'POST' and errand_form.is_valid():
            errand = errand_form.save(commit=False)
            errand.user = user
            errand.status = Errand.STATUS_PENDING
            errand.save()
            messages.success(
                request,
                'درخواست معموریت ثبت شد و برای بررسی به مدیر ارسال گردید. '
                'پس از تأیید، در گزارش‌ها محاسبه خواهد شد.',
            )
            return redirect(reverse('errands-my'))

    # فهرست کاربر — دو بخش جداگانه با حداکثر ۳ درخواست اخیر در هر صفحه
    # («در انتظار بررسی» و «بررسی‌شده») تا صفحه شلوغ نشود.
    mine = (
        Errand.objects
        .filter(user=user)
        .select_related('reviewed_by')
        .order_by('-start_time', '-id')
    )
    pending_page = Paginator(
        mine.filter(status=Errand.STATUS_PENDING), MY_PER_PAGE
    ).get_page(request.GET.get('pending_page'))
    done_page = Paginator(
        mine.exclude(status=Errand.STATUS_PENDING), MY_PER_PAGE
    ).get_page(request.GET.get('done_page'))

    # آمار بالای صفحه
    all_mine = Errand.objects.filter(user=user)
    counts = {
        'pending': all_mine.filter(status=Errand.STATUS_PENDING).count(),
        'approved': all_mine.filter(status=Errand.STATUS_APPROVED).count(),
        'rejected': all_mine.filter(status=Errand.STATUS_REJECTED).count(),
    }
    approved_seconds = services.total_seconds(
        (iv[1], iv[2])
        for e in all_mine.filter(status=Errand.STATUS_APPROVED)
        for iv in services.split_interval_by_day(e.start_time, e.end_time)
    )
    current = services.open_errand_now(user)

    return render(request, 'errands/my_errands.html', {
        'form': errand_form,
        'pending_page': pending_page,
        'done_page': done_page,
        'mine_count': mine.count(),
        'counts': counts,
        'approved_text': format_hours(approved_seconds),
        'current_errand': current,
        'earliest_date': timezone.localdate() - dt.timedelta(days=7),
        'backdate_limit': 7,
        'is_admin_mode': is_admin_mode,
    })


@login_required
@require_POST
def cancel_errand(request, pk):
    """کارمند درخواست «در انتظار» خودش را لغو می‌کند."""
    errand = get_object_or_404(Errand, pk=pk, user=request.user)
    if errand.cancel(request.user):
        messages.info(request, 'درخواست معموریت لغو شد.')
    else:
        messages.warning(
            request,
            'فقط درخواست‌های «در انتظار بررسی» را می‌توانید لغو کنید.',
        )
    return redirect(reverse('errands-my'))


# ============================================================
#  ۲) ادمین — صف بررسی
# ============================================================

@login_required
def admin_review(request):
    """صف بررسی معموریت‌ها: در انتظار / تأییدشده / ردشده."""
    if not can_access_admin(request):
        return redirect('not-authorised')

    qs, filter_form, date_from, date_to = _filter_qs(request)

    # پیش‌فرض: فقط «در انتظار بررسی» تا صف کاری شلوغ نشود
    if not request.GET.get('status'):
        qs = qs.filter(status=Errand.STATUS_PENDING)

    pending_qs = (
        Errand.objects
        .filter(status=Errand.STATUS_PENDING)
        .select_related('user')
        .order_by('start_time', 'id')
    )
    decided_qs = (
        qs
        .exclude(status=Errand.STATUS_PENDING)
        .select_related('user', 'reviewed_by')
        .order_by('-reviewed_at', '-id')
    )

    decided_paginator = Paginator(decided_qs, PER_PAGE)
    decided_page = decided_paginator.get_page(request.GET.get('page'))

    return render(request, 'errands/admin_review.html', {
        'pending_list': pending_qs,
        'pending_count': pending_qs.count(),
        'pending_text': format_hours(services.total_seconds(
            (iv[1], iv[2])
            for e in pending_qs
            for iv in services.split_interval_by_day(e.start_time, e.end_time)
        )),
        'decided_page': decided_page,
        'filter_form': filter_form,
        'review_form': ErrandReviewForm(),
        'approved_count': Errand.objects.filter(status=Errand.STATUS_APPROVED).count(),
        'rejected_count': Errand.objects.filter(status=Errand.STATUS_REJECTED).count(),
    })


@login_required
@require_POST
def review_errand(request, pk):
    """تأیید یا رد یک درخواست معموریت."""
    if not can_access_admin(request):
        return redirect('not-authorised')

    errand = get_object_or_404(Errand, pk=pk)
    form = ErrandReviewForm(request.POST)

    if not form.is_valid():
        messages.error(request, 'ورودی نامعتبر است.')
        return redirect(reverse('errands-admin') + '#errand-%d' % errand.pk)

    decision = form.cleaned_data['decision']
    note = (form.cleaned_data.get('note') or '').strip()

    # اگر مدت هنگام بررسی قابل محاسبه نیست، اجازهٔ تأیید نمی‌دهیم
    if decision == 'approve':
        if errand.end_time <= errand.start_time:
            messages.error(request, 'بازهٔ زمانی این درخواست نامعتبر است.')
            return redirect(reverse('errands-admin') + '#errand-%d' % errand.pk)
        if errand.approve(request.user, note=note):
            messages.success(
                request,
                'معموریت %s تأیید شد. از این پس در گزارش‌ها محاسبه می‌شود.'
                % errand.user.username,
            )
        else:
            messages.warning(request, 'این درخواست قبلاً بررسی شده است.')

    else:
        if errand.reject(request.user, note=note):
            messages.info(
                request,
                'معموریت %s رد شد و کارمند دلیل را می‌بیند.'
                % errand.user.username,
            )
        else:
            messages.warning(request, 'این درخواست قبلاً بررسی شده است.')

    return redirect(reverse('errands-admin'))


@login_required
def admin_edit(request, pk):
    """ادمین بازه یا توضیحات یک معموریت را اصلاح می‌کند.

    این صفحه برای وقتی است که کارمند بازه را اشتباه زده و خودش
    نمی‌تواند (چون بعد از تأیید دیگر قابل لغو نیست).
    """
    if not can_access_admin(request):
        return redirect('not-authorised')

    errand = get_object_or_404(Errand, pk=pk)
    form = ErrandAdminEditForm(request.POST or None, instance=errand)

    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'معموریت به‌روزرسانی شد.')
        return redirect(reverse('errands-admin'))

    return render(request, 'errands/admin_edit.html', {
        'form': form,
        'errand': errand,
        'start_value': _jalali_input(errand.start_time),
        'end_value': _jalali_input(errand.end_time),
    })


@login_required
@require_POST
def bulk_review(request):
    """تأیید یا رد گروهی چند درخواست انتخاب‌شده."""
    if not can_access_admin(request):
        return redirect('not-authorised')

    ids = request.POST.getlist('errand_ids')
    decision = request.POST.get('bulk_decision')
    note = (request.POST.get('bulk_note') or '').strip()

    if not ids:
        messages.warning(request, 'هیچ درخواستی انتخاب نشده است.')
        return redirect(reverse('errands-admin'))

    if decision == 'reject' and not note:
        messages.error(request, 'برای رد گروهی، نوشتن دلیل الزامی است.')
        return redirect(reverse('errands-admin'))

    done = 0
    skipped = 0
    for errand in Errand.objects.filter(pk__in=ids, status=Errand.STATUS_PENDING):
        if decision == 'approve':
            ok = errand.approve(request.user, note=note)
        else:
            ok = errand.reject(request.user, note=note)
        if ok:
            done += 1
        else:
            skipped += 1

    verb = 'تأیید' if decision == 'approve' else 'رد'
    messages.success(request, '%s درخواست %s شد.' % (done, verb))
    if skipped:
        messages.warning(request, '%s درخواست وضعیت معتبر نداشت و رد شد.' % skipped)
    return redirect(reverse('errands-admin'))


# ============================================================
#  ۳) گزارش / خروجی
# ============================================================

@login_required
def errand_list(request):
    """فهرست همهٔ معموریت‌ها در یک بازه — برای ادمین."""
    if not can_access_admin(request):
        return redirect('not-authorised')

    qs, filter_form, date_from, date_to = _filter_qs(request)
    if not request.GET.get('status'):
        qs = qs.filter(status=Errand.STATUS_APPROVED)

    rows = []
    for errand in qs.select_related('user', 'reviewed_by').order_by(
        '-start_time', 'user__username'
    ):
        rows.append({
            'errand': errand,
            'start_jalali': jalali_full(timezone.localtime(errand.start_time).date()),
            'start_clock': clock(errand.start_time),
            'end_clock': clock(errand.end_time),
            'duration': errand.duration_display(),
        })

    total_seconds = sum(r['errand'].duration_seconds for r in rows)

    return render(request, 'errands/errand_list.html', {
        'filter_form': filter_form,
        'rows': rows,
        'total_text': format_hours(total_seconds),
        'count': len(rows),
        'date_from': date_from,
        'date_to': date_to,
        'filter_qs': report_query_params(request),
    })


@login_required
def errand_export(request):
    """خروجی اکسل معموریت‌ها."""
    if not can_access_admin(request):
        return redirect('not-authorised')

    qs, _, _, _ = _filter_qs(request)
    if not request.GET.get('status'):
        qs = qs.filter(status=Errand.STATUS_APPROVED)

    rows = []
    for index, errand in enumerate(
        qs.select_related('user', 'reviewed_by').order_by('-start_time'), start=1
    ):
        employee = ('%s %s' % (errand.user.first_name,
                               errand.user.last_name)).strip() \
            or errand.user.username
        reviewer = '-'
        if errand.reviewed_by:
            reviewer = ('%s %s' % (errand.reviewed_by.first_name,
                                   errand.reviewed_by.last_name)).strip() \
                or errand.reviewed_by.username
        rows.append([
            fa_digits(index),
            '%s %s' % (jalali_full(timezone.localtime(errand.start_time).date()),
                       clock(errand.start_time)),
            '%s' % clock(errand.end_time),
            errand.duration_display(),
            employee,
            errand.reason,
            errand.destination or '-',
            errand.get_status_display(),
            reviewer,
        ])

    book = build_xlsx(
        sheet_name='معموریت‌ها',
        headers=['ردیف', 'شروع', 'پایان', 'مدت', 'کارمند', 'دلیل', 'مقصد',
                 'وضعیت', 'بررسی‌کننده'],
        rows=rows,
        widths=[8, 26, 12, 16, 20, 40, 22, 14, 20],
        rtl=True,
    )
    response = HttpResponse(book, content_type=XLSX_CONTENT_TYPE)
    response['Content-Disposition'] = 'attachment; filename="errands.xlsx"'
    return response