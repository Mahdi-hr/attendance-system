"""ویوهای اپ گزارش کارکرد پروژه‌ها."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from users.roles import can_access_admin

from . import services
from .excel import build_worklog_xlsx
from .forms import ProjectForm
from .models import Project, WorkLog, WorkLogMonth

XLSX_CONTENT_TYPE = (
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
)


def _parse_month(request):
    current_year, current_month = services.current_month()
    source = request.POST if request.method == 'POST' else request.GET
    try:
        year = int(source.get('year', current_year))
    except (TypeError, ValueError):
        year = current_year
    try:
        month = int(source.get('month', current_month))
    except (TypeError, ValueError):
        month = current_month
    if not (1 <= month <= 12):
        month = current_month
    if not (1300 <= year <= 1500):
        year = current_year
    return year, month


def _month_url(name, year, month):
    return '%s?year=%d&month=%d' % (reverse(name), year, month)


def _month_choices(year):
    current_year, _current_month = services.current_month()
    years = list(range(current_year - 2, current_year + 2))
    if year not in years:
        years.append(year)
        years.sort()
    months = [
        (index + 1, name)
        for index, name in enumerate(services.MONTHS_FA)
    ]
    return months, years


def _admin_guard(request):
    if not can_access_admin(request):
        return redirect('not-authorised')
    return None


# ============================================================
# کارمند
# ============================================================

@login_required
def my_worklog(request):
    year, month = _parse_month(request)
    month_state, _created = WorkLogMonth.objects.get_or_create(
        user=request.user, year=year, month=month,
        defaults={'status': WorkLogMonth.STATUS_DRAFT},
    )
    projects = services.projects_for_month(request.user, year, month)

    if request.method == 'POST':
        if month_state.is_submitted:
            messages.error(
                request,
                'این ماه ثبت نهایی شده است و قابل ویرایش نیست. '
                'برای اصلاح، ابتدا «بازگشت به ویرایش» را بزنید.',
            )
            return redirect(_month_url('worklog:my', year, month))

        ok, errors = services.save_allocations(
            request.user, year, month, projects, request.POST,
        )
        if ok:
            messages.success(request, 'کارکرد این ماه با موفقیت ذخیره شد.')
            return redirect(_month_url('worklog:my', year, month))
        for error in errors:
            messages.error(request, error)
        matrix = services.month_matrix(
            request.user, year, month, projects,
        )
        matrix = services.overlay_matrix(matrix, projects, request.POST)
    else:
        matrix = services.month_matrix(
            request.user, year, month, projects,
        )

    month_names, years = _month_choices(year)
    return render(request, 'worklog/my_worklog.html', {
        'matrix': matrix,
        'month_state': month_state,
        'year': year,
        'month': month,
        'month_label': services.month_label(year, month),
        'month_names': month_names,
        'years': years,
        'editable': not month_state.is_submitted,
        'is_admin': can_access_admin(request),
        'worked_json': {
            str(day['day']): float(day['worked'])
            for day in matrix['days']
        },
    })


@login_required
@require_POST
def submit_month(request):
    year, month = _parse_month(request)
    projects = services.projects_for_month(request.user, year, month)

    # اگر کاربر بدون ذخیرهٔ پیش‌نویس مستقیم «ثبت نهایی» را زده باشد،
    # ابتدا همان مقادیر فرم ذخیره و اعتبارسنجی می‌شوند.
    if any(key.startswith(('p', 'other_')) for key in request.POST):
        ok, errors = services.save_allocations(
            request.user, year, month, projects, request.POST,
        )
        if not ok:
            for error in errors:
                messages.error(request, error)
            return redirect(_month_url('worklog:my', year, month))

    start, end, _count = services.month_bounds(year, month)
    has_logs = WorkLog.objects.filter(
        user=request.user, date__gte=start, date__lte=end,
    ).exists()
    if not has_logs:
        messages.error(
            request, 'قبل از ثبت نهایی، حداقل یک ساعت کارکرد ثبت کنید.',
        )
        return redirect(_month_url('worklog:my', year, month))

    month_state, _created = WorkLogMonth.objects.get_or_create(
        user=request.user, year=year, month=month,
        defaults={'status': WorkLogMonth.STATUS_DRAFT},
    )
    month_state.status = WorkLogMonth.STATUS_SUBMITTED
    month_state.submitted_at = timezone.now()
    month_state.save(update_fields=['status', 'submitted_at', 'updated_at'])
    messages.success(
        request,
        'کارکرد این ماه ثبت نهایی شد. تا زمانی که آن را بازگشایی نکنید '
        'قابل ویرایش نیست.',
    )
    return redirect(_month_url('worklog:my', year, month))


@login_required
@require_POST
def reopen_month(request):
    year, month = _parse_month(request)
    month_state = WorkLogMonth.objects.filter(
        user=request.user, year=year, month=month,
    ).first()
    if month_state and month_state.is_submitted:
        month_state.status = WorkLogMonth.STATUS_DRAFT
        month_state.submitted_at = None
        month_state.save(update_fields=['status', 'submitted_at', 'updated_at'])
        messages.warning(request, 'ماه به حالت پیش‌نویس برگشت و قابل ویرایش است.')
    return redirect(_month_url('worklog:my', year, month))


@login_required
def my_export(request):
    year, month = _parse_month(request)
    content, filename = build_worklog_xlsx(request.user, year, month)
    response = HttpResponse(content, content_type=XLSX_CONTENT_TYPE)
    response['Content-Disposition'] = 'attachment; filename="%s"' % filename
    return response


# ============================================================
# مدیر
# ============================================================

@login_required
def admin_list(request):
    guard = _admin_guard(request)
    if guard:
        return guard
    year, month = _parse_month(request)
    rows = services.admin_month_rows(year, month)
    months, years = _month_choices(year)
    return render(request, 'worklog/admin_list.html', {
        'rows': rows,
        'year': year,
        'month': month,
        'month_label': services.month_label(year, month),
        'month_names': months,
        'years': years,
    })


@login_required
def admin_detail(request, user_id):
    guard = _admin_guard(request)
    if guard:
        return guard
    target_user = get_object_or_404(
        services.User, pk=user_id,
    )
    year, month = _parse_month(request)
    projects = services.projects_for_month(target_user, year, month)
    matrix = services.month_matrix(target_user, year, month, projects)
    month_state = WorkLogMonth.objects.filter(
        user=target_user, year=year, month=month,
    ).first()
    months, years = _month_choices(year)
    return render(request, 'worklog/admin_detail.html', {
        'target_user': target_user,
        'display_name': services.user_display(target_user),
        'matrix': matrix,
        'month_state': month_state,
        'year': year,
        'month': month,
        'month_label': services.month_label(year, month),
        'month_names': months,
        'years': years,
    })


@login_required
def admin_export(request, user_id):
    guard = _admin_guard(request)
    if guard:
        return guard
    target_user = get_object_or_404(services.User, pk=user_id)
    year, month = _parse_month(request)
    content, filename = build_worklog_xlsx(target_user, year, month)
    response = HttpResponse(content, content_type=XLSX_CONTENT_TYPE)
    response['Content-Disposition'] = 'attachment; filename="%s"' % filename
    return response


# ============================================================
# مدیریت پروژه‌ها (فقط مدیر)
# ============================================================

@login_required
def project_list(request):
    guard = _admin_guard(request)
    if guard:
        return guard
    projects = (
        Project.objects
        .annotate(worklog_count=Count('worklogs'))
        .order_by('sort_order', 'name')
    )
    return render(request, 'worklog/project_list.html', {
        'projects': projects,
    })


@login_required
def project_create(request):
    guard = _admin_guard(request)
    if guard:
        return guard
    form = ProjectForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'پروژه با موفقیت اضافه شد.')
        return redirect('worklog:projects')
    return render(request, 'worklog/project_form.html', {
        'form': form,
        'title': 'افزودن پروژه',
        'is_edit': False,
    })


@login_required
def project_edit(request, pk):
    guard = _admin_guard(request)
    if guard:
        return guard
    project = get_object_or_404(Project, pk=pk)
    form = ProjectForm(request.POST or None, instance=project)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'پروژه به‌روزرسانی شد.')
        return redirect('worklog:projects')
    return render(request, 'worklog/project_form.html', {
        'form': form,
        'project': project,
        'title': 'ویرایش پروژه',
        'is_edit': True,
    })


@login_required
@require_POST
def project_toggle(request, pk):
    guard = _admin_guard(request)
    if guard:
        return guard
    project = get_object_or_404(Project, pk=pk)
    project.is_active = not project.is_active
    project.save(update_fields=['is_active', 'updated_at'])
    messages.success(
        request,
        'پروژه «%s» %s شد.' % (
            project.name,
            'فعال' if project.is_active else 'غیرفعال',
        ),
    )
    return redirect('worklog:projects')
