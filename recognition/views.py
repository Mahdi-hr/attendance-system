from django.shortcuts import render,redirect,get_object_or_404
from .forms import (
    usernameForm, BreakForm, DateRangeForm, EmployeeDateRangeForm,
    CheckoutErrorFilterForm,
)
from django.contrib import messages
from django.contrib.auth.models import User
import time
from django.contrib.auth.decorators import login_required
import datetime
from users.models import Present, Time, Break, CheckOutError, ExitReason
from users.roles import can_access_admin, is_pure_admin
from django.http import JsonResponse, StreamingHttpResponse, HttpResponse
from django.views.decorators.csrf import ensure_csrf_cookie, csrf_exempt
from django.views.decorators.http import require_POST
from django.core.paginator import Paginator
from django.urls import reverse
from urllib.parse import urlencode
from django.db import models
from django.utils import timezone
from .camera import (
    camera_manager, enumerate_cameras, list_video_devices, get_camera_source,
    camera_failure_message,
    KIND_IN, KIND_OUT, KIND_BREAK_OUT, KIND_BREAK_IN, KIND_DATASET, KIND_LABELS,
)
from .checkout_errors import (
    auto_fix_missing_checkouts_throttled,
)
from .reports import (
    daily_rows, range_summary, format_hours, weekly_summary, monthly_summary,
    fa_digits, clock, jalali_full, timeline_text, per_user_summary,
)
from .excel_export import build_xlsx

PER_PAGE = 30

XLSX_CONTENT_TYPE = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


# ============================================================================
# کمک‌کننده‌های صف انتظار دوربین
# ----------------------------------------------------------------------------
# نکتهٔ کلیدی طراحی: هر نشست دوربین به «کلید نشست مرورگر» گره خورده است، نه فقط
# به نام کاربر. اگر کاربر الف با یک تب و با تب دوم (یا یک مرورگر دیگر) بیاید، دو
# نفر مستقل در صف می‌نشینند و هرکدام نوبت خودشان را می‌گیرند — برخلاف حالت قبل
# که نام کاربر ملا بود و دو تبِ یک نفر، دوربین را برای هم قفل می‌کرد.
# ============================================================================
INTENT_SESSION_KEY = 'camera_queue_intent'


def _session_key(request):
    """کلید نشست Django برای این مرورگر؛ لازم برای تفکیک افراد در صف."""
    if not request.session.session_key:
        request.session.save()
    return request.session.session_key or ''


def _camera_ticket(request, kind=None):
    """تیکت زندهٔ این کاربر (صاحب دوربین یا در صف)، یا None.

    تمام اندپوینت‌های دوربین باید از همین تیکت استفاده کنند تا یک کاربر نتواند
    وضعیت، فریم یا دکمهٔ لغو نشست کاربر دیگر را لمس کند.
    """
    return camera_manager.ticket_of(request.user.username, _session_key(request), kind)


def _remember_intent(request, kind, view_name, params=None):
    """意图 را در نشست ذخیره می‌کند تا صفحهٔ «صف انتظار» بداند بعد از نوبت شدن
    کاربر باید به کدام صفحه برود و با چه پارامترهایی."""
    request.session[INTENT_SESSION_KEY] = {
        'kind': kind, 'view': view_name, 'params': params or {},
    }


def _pop_intent(request):
    intent = request.session.pop(INTENT_SESSION_KEY, None)
    return intent


def _start_or_queue(request, kind, template, **params):
    """نقطهٔ مشترک همهٔ «شروع دوربین»ها.

    سه حالت دارد:
      granted   → صفحهٔ استریم رندر می‌شود
      finishing → کاربر صفحه را رفرش کرده؛ همان وضعیت قبلی نشان داده می‌شود
      queued    → به صفحهٔ «صف انتظار» هدایت می‌شود (به‌جای رد کردنِ کاربر)
    """
    username = request.user.username
    status, ticket = camera_manager.acquire(
        username, _session_key(request), kind, **params)

    if status == 'error':
        messages.error(request, camera_failure_message())
        return redirect('dashboard')

    if status == 'queued':
        _remember_intent(request, kind, template, params)
        return redirect('camera-queue')

    # granted یا finishing
    context = {'mode': kind, 'username': username, 'ticket': ticket,
               'kind_label': KIND_LABELS.get(kind, '')}
    if kind in (KIND_BREAK_OUT, KIND_BREAK_IN):
        context['mode'] = kind
    else:
        context['mode'] = kind  # 'in' / 'out'
    return render(request, template, context)


@login_required
def camera_queue(request):
    """صفحهٔ «صف انتظار دوربین».

    کاربری که دوربین آزاد نبوده اینجا می‌ماند و هر ۲ ثانیه وضعیتش را می‌پرسد.
    به‌محض اینکه نوبتش شود، خودکار به صفحهٔ استریم هدایت می‌شود — بدون هیچ
    کلیک اضافه‌ای.
    """
    # فقط «می‌خوانیم»، پاک نمی‌کنیم: اندپوینت وضعیت صف هم به این intent نیاز دارد
    # تا بعد از نوبت شدن بداند کاربر باید به کدام صفحه برود.
    intent = request.session.get(INTENT_SESSION_KEY) or {}
    username = (intent or {}).get('params', {}).get('username') or request.user.username
    snapshot = camera_manager.queue_snapshot(request.user.username, _session_key(request))
    target = _intent_target_url(intent)
    if target is None:
        target = reverse('dashboard')
    return render(request, 'recognition/camera_queue.html', {
        'snapshot': snapshot,
        'target_url': target,
        'kind': (intent or {}).get('kind'),
        'kind_label': KIND_LABELS.get((intent or {}).get('kind'), ''),
        'target_username': username,
        'queue_url': reverse('camera-queue'),
        'cancel_url': reverse('camera-queue-leave'),
        'max_wait_sec': snapshot.get('max_wait_sec', 1200),
    })


def _intent_target_url(intent):
    """URL ای که بعد از نوبت شدن باید به آن رفت."""
    if not intent:
        return None
    kind = intent.get('kind')
    if kind == KIND_IN:
        return reverse('mark-your-attendance')
    if kind == KIND_OUT:
        return reverse('mark-your-attendance-out')
    if kind == KIND_BREAK_OUT:
        return reverse('mark-break-out')
    if kind == KIND_BREAK_IN:
        return reverse('mark-break-in')
    if kind == KIND_DATASET:
        return reverse('register-photo-stream')
    return None


@login_required
def camera_queue_status(request):
    """پول‌زدن صفحهٔ انتظار.

    اگر کاربر صاحب دوربین شده باشد `redirect_to` برمی‌گردد تا JS خودش هدایت
    کند؛ در غیر این صورت اطلاعات نمایشی صف برمی‌گردد.
    """
    username = request.user.username
    session_key = _session_key(request)
    snapshot = camera_manager.queue_snapshot(username, session_key)

    intent = request.session.get(INTENT_SESSION_KEY) or {}
    redirect_to = None
    if snapshot['is_owner']:
        # نوبتش شده؛ پارامترهای قالب را با همان مقادیر برمی‌گردانیم
        redirect_to = _intent_target_url(intent) or reverse('dashboard')
        _pop_intent(request)
    elif snapshot['state'] == 'finishing':
        # کارش تمام شده و فقط باید نتیجه را ببیند
        redirect_to = _intent_target_url(intent) or reverse('dashboard')
        _pop_intent(request)
    elif snapshot['state'] == 'idle' and intent:
        # تیکتش از صف افتاده (صفحه بسته شده / مهلت گذشت) → برگردد و دوباره تلاش کند
        _pop_intent(request)
        redirect_to = _intent_target_url(intent)

    payload = dict(snapshot)
    payload['redirect_to'] = redirect_to
    payload['kind'] = intent.get('kind')
    return JsonResponse(payload)


@login_required
@csrf_exempt
def camera_queue_leave(request):
    """خروج کاربر از صف.

    csrf_exempt چون با `navigator.sendBeacon` در beforeunload صدا زده می‌شود و
    توکن CSRF ندارد. بی‌خطر است: فقط تیکت خودِ همین کاربر حذف می‌شود.
    """
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST required'}, status=405)
    left = camera_manager.leave_queue(request.user.username, _session_key(request))
    _pop_intent(request)
    return JsonResponse({'ok': True, 'left_queue': left})


def username_present(username):
	return User.objects.filter(username=username).exists()


def total_number_employees():
	return User.objects.filter(is_superuser=False).count()


def employees_present_today():
	today = datetime.date.today()
	return Present.objects.filter(date=today, present=True).count()


def default_report_range():
	today = datetime.date.today()
	return today - datetime.timedelta(days=30), today


def employees_queryset():
	return User.objects.filter(is_superuser=False)


def report_query_params(request):
	"""Query string (minus page/export/print) to keep filters across links."""
	params = {}
	for key, value in request.GET.items():
		if key in ('page', 'export', 'print'):
			continue
		params[key] = value
	return urlencode(params)


def group_to_rows(rows):
	"""Flatten daily rows into export table (red, date, user, ..., duration)."""
	out = []
	for index, r in enumerate(rows, start=1):
		exit_reason_display = '-'
		if r.get('exit_reason'):
			exit_reason_display = r['exit_reason'].title
		elif r.get('exit_reason_text'):
			exit_reason_display = r['exit_reason_text']
		out.append([
			fa_digits(index),
			jalali_full(r['date']),
			r['username'],
			'حاضر' if r['present'] else 'غایب',
			timeline_text(r),
			clock(r['first_in']) if r['first_in'] else '-',
			clock(r['last_out']) if r['last_out'] else '-',
			r['work_text'],
			r.get('errand_text', '-'),         # ← NEW: مدت معموریت
			r['break_text'] if r['break_text'] != '-' else '-',
			exit_reason_display,
		])
	return out


EXPORT_HEADERS = ['ردیف', 'تاریخ', 'کارمند', 'وضعیت', 'ورود و خروج', 'ساعت ورود', 'ساعت خروج', 'مدت حضور', 'مدت معموریت', 'استراحت', 'دلیل خروج']
EXPORT_WIDTHS = [8, 24, 14, 10, 44, 12, 12, 16, 12, 24, 20]


def build_export_response(prefix, date_from, date_to, rows):
	headers = EXPORT_HEADERS
	data = group_to_rows(rows)
	book = build_xlsx(
		sheet_name='گزارش حضور',
		headers=headers,
		rows=data,
		widths=EXPORT_WIDTHS,
		rtl=True,
	)
	suffix = '%s_%s' % (date_from or 'all', date_to or 'any')
	filename = '%s_%s.xlsx' % (prefix, suffix)
	response = HttpResponse(book, content_type=XLSX_CONTENT_TYPE)
	response['Content-Disposition'] = 'attachment; filename="%s"' % filename
	return response


def group_rows_by_date(rows):
	grouped = []
	for row in rows:
		if grouped and grouped[-1]['date'] == row['date']:
			group = grouped[-1]
		else:
			group = {'date': row['date'], 'rows': [], 'present_count': 0, 'day_seconds': 0}
			grouped.append(group)
		group['rows'].append(row)
		if row['present']:
			group['present_count'] += 1
		group['day_seconds'] += row['work_seconds']
	for group in grouped:
		group['day_text'] = format_hours(group['day_seconds'])
	return grouped
# Create your views here.
def home(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    return redirect('login')

@login_required
def dashboard(request):
    auto_fix_missing_checkouts_throttled()
    pending_errors = CheckOutError.objects.filter(status='pending').count()
    if can_access_admin(request):
        from errands.models import Errand
        return render(request, 'recognition/admin_dashboard.html', {
            'pending_errors': pending_errors,
            'pending_errand_count': Errand.objects.filter(
                status=Errand.STATUS_PENDING
            ).count(),
        })
    else:
        today = datetime.date.today()
        user = request.user
        
        today_present = Present.objects.filter(user=user, date=today).first()
        today_times = Time.objects.filter(user=user, date=today).order_by('time')
        today_breaks = Break.objects.filter(user=user, date=today).order_by('break_out')
        
        # Find latest check-in and check-out
        check_in_time = None
        check_out_time = None
        for t in today_times:
            if not t.out:
                check_in_time = t.time  # Keep updating to get latest check-in
            else:
                check_out_time = t.time  # Keep updating to get latest check-out
        
        # User is checked in if latest event is check-in (no matching check-out after it)
        is_checked_in = check_in_time is not None and (check_out_time is None or check_in_time > check_out_time)
        
        last_break = today_breaks.last()
        is_on_break = False
        break_start = None
        if last_break and last_break.break_out and not last_break.break_in:
            is_on_break = True
            break_start = last_break.break_out
        
        # Calculate work hours
        work_seconds = 0
        if check_in_time:
            # Find the check-out that pairs with the latest check-in
            paired_check_out = None
            if check_out_time and check_out_time > check_in_time:
                paired_check_out = check_out_time
            end_time = paired_check_out or timezone.now()
            # Ensure check_in_time is timezone-aware before subtraction
            if check_in_time and timezone.is_naive(check_in_time):
                check_in_time = timezone.make_aware(check_in_time)
            if isinstance(end_time, datetime.datetime) and isinstance(check_in_time, datetime.datetime):
                work_seconds = int((end_time - check_in_time).total_seconds())
            for b in today_breaks:
                if b.break_out and b.break_in:
                    work_seconds -= int((b.break_in - b.break_out).total_seconds())
        
        from errands import services as errand_services
        from errands.models import Errand

        my_errands = Errand.objects.filter(user=user)
        context = {
            'check_in_time': check_in_time,
            'check_out_time': paired_check_out if 'paired_check_out' in locals() else check_out_time,
            'is_checked_in': is_checked_in,
            'is_on_break': is_on_break,
            'break_start': break_start,
            'work_text': format_hours(max(0, work_seconds)),
            # ---- معموریت ----
            'current_errand': errand_services.open_errand_now(user),
            'my_errand_counts': {
                'pending': my_errands.filter(
                    status=Errand.STATUS_PENDING).count(),
                'approved': my_errands.filter(
                    status=Errand.STATUS_APPROVED).count(),
                'rejected': my_errands.filter(
                    status=Errand.STATUS_REJECTED).count(),
            },
        }
        return render(request, 'recognition/employee_dashboard.html', context)

@login_required
def add_photos(request):
	if not can_access_admin(request):
		return redirect('not-authorised')
	if request.method=='POST':
		form=usernameForm(request.POST)
		data = request.POST.copy()
		username=data.get('username')
		if username_present(username):
			u = User.objects.get(username=username)
			return redirect('register-photo-capture', user_id=u.id)
		else:
			messages.warning(request, f'No such username found. Please register employee first.')
			return redirect('dashboard')
	else:
		form=usernameForm()
	return render(request,'recognition/add_photos.html', {'form' : form})



def train_model():
    """Reusable model training function. Returns (count, message)."""
    from .camera import train_gallery
    return train_gallery()


@login_required
def train(request):
    if not can_access_admin(request):
        return redirect('not-authorised')

    count, message = train_model()
    if count > 0:
        camera_manager.load_model()
    messages.success(request, message)

    return render(request, "recognition/train.html")


@login_required
@ensure_csrf_cookie
def register_photo_capture(request, user_id):
    """Photocapture page: camera picker + preview + ready button before saving."""
    if not can_access_admin(request):
        return redirect('not-authorised')
    try:
        target_user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        messages.error(request, 'کاربر یافت نشد.')
        return redirect('users-list')
    return render(request, 'recognition/register_photo_stream.html', {
        'username': target_user.username,
        'cameras': enumerate_cameras(),
        'camera_devices': list_video_devices(),
        'camera_source': get_camera_source(),
    })


@login_required
@ensure_csrf_cookie
def dataset_camera_start(request):
    if not can_access_admin(request):
        return JsonResponse({'ok': False, 'error': 'unauthorized'})
    if request.method == 'POST':
        try:
            camera_index = int(request.POST.get('camera_index', 0))
        except (TypeError, ValueError):
            camera_index = 0
        username = request.POST.get('username', '')
        if not username_present(username):
            return JsonResponse({'ok': False, 'error': 'نام کاربری نامعتبر است.'})
        status, ticket = camera_manager.acquire(
            request.user.username, _session_key(request), KIND_DATASET,
            target_username=username, num_samples=50, auto_train=True,
            camera_index=camera_index)
        if status == 'queued':
            _remember_intent(request, KIND_DATASET, 'dataset_camera_start', {
                'username': username, 'camera_index': camera_index})
            snap = camera_manager.queue_snapshot(
                request.user.username, _session_key(request))
            return JsonResponse({
                'ok': False, 'queued': True, 'ticket': ticket,
                'position': snap['position'], 'queue_length': snap['queue_length'],
                'error': 'دوربین در حال استفاده است؛ در صف منتظر می‌مانید.',
                'queue_url': reverse('camera-queue'),
            })
        if status == 'error':
            return JsonResponse({'ok': False, 'error': camera_failure_message()})
        return JsonResponse({'ok': True, 'ticket': ticket})
    return JsonResponse({'ok': False, 'error': 'bad request'})


@login_required
@ensure_csrf_cookie
def dataset_camera_confirm(request):
    if not can_access_admin(request):
        return JsonResponse({'ok': False, 'error': 'unauthorized'})
    ticket = _camera_ticket(request, kind=KIND_DATASET)
    ok = camera_manager.confirm_dataset_capture(ticket)
    return JsonResponse({'ok': ok})


@login_required
@csrf_exempt
def dataset_camera_cancel(request):
    if not can_access_admin(request):
        return JsonResponse({'ok': False, 'error': 'unauthorized'})
    # csrf_exempt: مثل attendance_cancel — توسط sendBeacon در beforeunload صدا
    # زده می‌شود و توکن CSRF ندارد؛ فقط حالت دوربین را آزاد می‌کند.
    ticket = _camera_ticket(request, kind=KIND_DATASET)
    camera_manager.cancel_dataset_capture(ticket)
    return JsonResponse({'ok': True})


@login_required
@csrf_exempt
def attendance_cancel(request):
    """Cancel attendance process - releases camera and returns to dashboard.

    csrf_exempt: این اندپوینت فقط یک «لغو/آزادسازی» بی‌خطر است و از طریق
    navigator.sendBeacon در رویداد beforeunload صدا زده می‌شود که امکان
    ارسال توکن CSRF را ندارد. بدون این، دوربین بعد از بستن صفحه قفل می‌ماند.
    """
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)
    # فقط نشست متعلق به همین کاربر لغو می‌شود. قبلاً هر کاربری با بستن صفحهٔ خودش
    # می‌توانست دوربین کاربر دیگری را هم آزاد کند و سرور را خراب کند.
    success = camera_manager.cancel_attendance(request.user.username, _session_key(request))
    return JsonResponse({'success': success})


@login_required
def not_authorised(request):
	return render(request,'recognition/not_authorised.html')



@login_required
def view_attendance_home(request):
	total_num_of_emp = total_number_employees()
	emp_present_today = employees_present_today()
	date_from, date_to = default_report_range()
	month_rows = daily_rows(employees_queryset(), date_from, date_to)
	present_days, month_total_seconds, _ = range_summary(month_rows)
	today_rows = daily_rows(employees_queryset(), date_to, date_to)
	return render(request, "recognition/view_attendance_home.html", {
		'total_num_of_emp': total_num_of_emp,
		'emp_present_today': emp_present_today,
		'today_rows': today_rows,
		'month_present_days': present_days,
		'month_total_text': format_hours(month_total_seconds),
	})


@login_required
def view_attendance_date(request):
	if not can_access_admin(request):
		return redirect('not-authorised')

	form = DateRangeForm(request.GET)
	date_from = date_to = None
	if form.is_bound:
		if form.is_valid():
			date_from = form.cleaned_data.get('date_from')
			date_to = form.cleaned_data.get('date_to')
			if date_from and date_to and date_to < date_from:
				messages.warning(request, 'بازه تاریخ نامعتبر است. تاریخ پایان باید پس از تاریخ شروع باشد.')
				date_from = date_to = None
		else:
			messages.error(request, 'فیلتر تاریخ نامعتبر است.')

	rows = daily_rows(employees_queryset(), date_from, date_to)
	days_present, total_seconds, person_count = range_summary(rows)
	total_text = format_hours(total_seconds)
	avg_text = format_hours(total_seconds // days_present) if days_present else '-'

	paginator = Paginator(rows, PER_PAGE)
	page_obj = paginator.get_page(request.GET.get('page'))

	return render(request, 'recognition/view_attendance_date.html', {
		'form': form,
		'page_obj': page_obj,
		'filter_qs': report_query_params(request),
		'date_from': date_from,
		'date_to': date_to,
		'days_present': days_present,
		'total_text': total_text,
		'avg_text': avg_text,
		'person_count': person_count,
		'total_rows': len(rows),
	})


@login_required
def view_attendance_date_export(request):
	if not can_access_admin(request):
		return redirect('not-authorised')
	form = DateRangeForm(request.GET)
	date_from = date_to = None
	if form.is_valid():
		date_from = form.cleaned_data.get('date_from')
		date_to = form.cleaned_data.get('date_to')
		if date_from and date_to and date_to < date_from:
			date_from = date_to = None
	rows = daily_rows(employees_queryset(), date_from, date_to)
	return build_export_response('attendance_report', date_from, date_to, rows)


@login_required
def view_attendance_date_print(request):
	if not can_access_admin(request):
		return redirect('not-authorised')
	form = DateRangeForm(request.GET)
	date_from = date_to = None
	if form.is_valid():
		date_from = form.cleaned_data.get('date_from')
		date_to = form.cleaned_data.get('date_to')
		if date_from and date_to and date_to < date_from:
			date_from = date_to = None
	rows = daily_rows(employees_queryset(), date_from, date_to)
	days_present, total_seconds, person_count = range_summary(rows)
	user_summaries = per_user_summary(rows)
	return render(request, 'recognition/view_attendance_date_print.html', {
		'rows': rows,
		'date_from': date_from,
		'date_to': date_to,
		'days_present': days_present,
		'total_text': format_hours(total_seconds),
		'person_count': person_count,
		'average_text': format_hours(total_seconds // days_present) if days_present else '-',
		'generated_at': datetime.datetime.now(),
		'user_summaries': user_summaries,
	})


@login_required
def view_attendance_employee(request):
	if not can_access_admin(request):
		return redirect('not-authorised')

	username = (request.GET.get('username') or '').strip()
	form = EmployeeDateRangeForm(request.GET) if username else EmployeeDateRangeForm()
	date_from = date_to = None
	if username and form.is_bound:
		if form.is_valid():
			date_from = form.cleaned_data.get('date_from')
			date_to = form.cleaned_data.get('date_to')
			if date_from and date_to and date_to < date_from:
				messages.warning(request, 'بازه تاریخ نامعتبر است. تاریخ پایان باید پس از تاریخ شروع باشد.')
				date_from = date_to = None
		else:
			messages.error(request, 'فیلتر تاریخ نامعتبر است.')

	show_report = bool(username) and username_present(username)
	rows = []
	total_text = avg_text = max_day_text = '-'
	days_present = 0
	employee_not_found = bool(username) and not show_report

	if show_report:
		user = User.objects.get(username=username)
		rows = daily_rows([user], date_from, date_to)
		days_present, total_seconds, _ = range_summary(rows)
		total_text = format_hours(total_seconds)
		if days_present:
			avg_text = format_hours(total_seconds // days_present)
		if rows:
			max_day_text = format_hours(max(r['work_seconds'] for r in rows))

	employee_list = []
	for emp in employees_queryset():
		present_count = Present.objects.filter(user=emp, present=True).count()
		employee_list.append({
			'user': emp,
			'username': emp.username,
			'full_name': ('%s %s' % (emp.first_name, emp.last_name)).strip() or emp.username,
			'attendance_count': present_count,
		})

	paginator = Paginator(rows, PER_PAGE)
	page_obj = paginator.get_page(request.GET.get('page'))

	return render(request, 'recognition/view_attendance_employee.html', {
		'form': form,
		'page_obj': page_obj,
		'filter_qs': report_query_params(request),
		'username': username,
		'days_present': days_present,
		'total_text': total_text,
		'avg_text': avg_text,
		'max_day_text': max_day_text,
		'show_report': show_report,
		'employee_not_found': employee_not_found,
		'employee_list': employee_list,
		'weekly': weekly_summary(rows),
		'monthly': monthly_summary(rows),
		'total_rows': len(rows),
	})


@login_required
def view_attendance_employee_export(request):
	if not can_access_admin(request):
		return redirect('not-authorised')
	username = (request.GET.get('username') or '').strip()
	if not username or not username_present(username):
		return redirect('view-attendance-employee')
	date_from = date_to = None
	form = EmployeeDateRangeForm(request.GET)
	if form.is_valid():
		date_from = form.cleaned_data.get('date_from')
		date_to = form.cleaned_data.get('date_to')
		if date_from and date_to and date_to < date_from:
			date_from = date_to = None
	rows = daily_rows([User.objects.get(username=username)], date_from, date_to)
	return build_export_response(username, date_from, date_to, rows)


@login_required
def view_attendance_employee_print(request):
	if not can_access_admin(request):
		return redirect('not-authorised')
	username = (request.GET.get('username') or '').strip()
	if not username or not username_present(username):
		return redirect('view-attendance-employee')
	date_from = date_to = None
	form = EmployeeDateRangeForm(request.GET)
	if form.is_valid():
		date_from = form.cleaned_data.get('date_from')
		date_to = form.cleaned_data.get('date_to')
		if date_from and date_to and date_to < date_from:
			date_from = date_to = None
	rows = daily_rows([User.objects.get(username=username)], date_from, date_to)
	days_present, total_seconds, _ = range_summary(rows)
	return render(request, 'recognition/view_attendance_employee_print.html', {
		'username': username,
		'rows': rows,
		'date_from': date_from,
		'date_to': date_to,
		'days_present': days_present,
		'total_text': format_hours(total_seconds),
		'average_text': format_hours(total_seconds // days_present) if days_present else '-',
		'max_day_text': format_hours(max(r['work_seconds'] for r in rows)) if rows else '-',
		'weekly': weekly_summary(rows),
		'monthly': monthly_summary(rows),
		'generated_at': datetime.datetime.now(),
	})


@login_required
def view_my_attendance_employee_login(request):
	if is_pure_admin(request.user):
		return redirect('not-authorised')

	form = DateRangeForm(request.GET)
	date_from = date_to = None
	if form.is_bound:
		if form.is_valid():
			date_from = form.cleaned_data.get('date_from')
			date_to = form.cleaned_data.get('date_to')
			if date_from and date_to and date_to < date_from:
				messages.warning(request, 'بازه تاریخ نامعتبر است. تاریخ پایان باید پس از تاریخ شروع باشد.')
				date_from = date_to = None
		else:
			messages.error(request, 'فیلتر تاریخ نامعتبر است.')

	rows = daily_rows([request.user], date_from, date_to)
	days_present, total_seconds, _ = range_summary(rows)
	total_text = format_hours(total_seconds)
	avg_text = format_hours(total_seconds // days_present) if days_present else '-'
	max_day_text = format_hours(max(r['work_seconds'] for r in rows)) if rows else '-'

	paginator = Paginator(rows, PER_PAGE)
	page_obj = paginator.get_page(request.GET.get('page'))

	return render(request, 'recognition/view_my_attendance_employee_login.html', {
		'form': form,
		'rows': rows,
		'page_obj': page_obj,
		'filter_qs': report_query_params(request),
		'days_present': days_present,
		'total_text': total_text,
		'avg_text': avg_text,
		'max_day_text': max_day_text,
		'weekly': weekly_summary(rows),
		'monthly': monthly_summary(rows),
		'total_rows': len(rows),
	})


@login_required
def view_my_attendance_export(request):
	if is_pure_admin(request.user):
		return redirect('not-authorised')
	form = DateRangeForm(request.GET)
	date_from = date_to = None
	if form.is_valid():
		date_from = form.cleaned_data.get('date_from')
		date_to = form.cleaned_data.get('date_to')
		if date_from and date_to and date_to < date_from:
			date_from = date_to = None
	rows = daily_rows([request.user], date_from, date_to)
	return build_export_response(request.user.username, date_from, date_to, rows)


@login_required
def view_my_attendance_print(request):
	if is_pure_admin(request.user):
		return redirect('not-authorised')
	username = request.user.username
	form = DateRangeForm(request.GET)
	date_from = date_to = None
	if form.is_valid():
		date_from = form.cleaned_data.get('date_from')
		date_to = form.cleaned_data.get('date_to')
		if date_from and date_to and date_to < date_from:
			date_from = date_to = None
	rows = daily_rows([request.user], date_from, date_to)
	days_present, total_seconds, _ = range_summary(rows)
	return render(request, 'recognition/view_attendance_employee_print.html', {
		'username': username,
		'rows': rows,
		'date_from': date_from,
		'date_to': date_to,
		'days_present': days_present,
		'total_text': format_hours(total_seconds),
		'average_text': format_hours(total_seconds // days_present) if days_present else '-',
		'max_day_text': format_hours(max(r['work_seconds'] for r in rows)) if rows else '-',
		'weekly': weekly_summary(rows),
		'monthly': monthly_summary(rows),
		'generated_at': datetime.datetime.now(),
	})


@login_required
def mark_your_attendance(request):
    return _start_or_queue(request, KIND_IN, 'recognition/mark_attendance_stream.html')


@login_required
def mark_your_attendance_out(request):
    return _start_or_queue(request, KIND_OUT, 'recognition/mark_attendance_stream.html')


_PLACEHOLDER_JPEG = b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.\x27 ",#\x1c\x1c(7),01444\x1f\x2744444444444444\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xda\x00\x08\x01\x01\x00\x00?\x00T\xdb\x9e\xa9\xff\xd9'


def gen_frames(ticket=None):
    """استریم MJPEG.

    فقط صاحب نشست (`ticket`) فریم واقعی وبکم را می‌گیرد؛ بقیه تصویرِ «در حال
    استفاده» را می‌بینند. بدون این، دو کاربر هم‌زمان تصویر هم را می‌دیدند.
    """
    while True:
        frame = camera_manager.get_frame(ticket=ticket)
        if frame is None:
            frame = _PLACEHOLDER_JPEG
        yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
        time.sleep(0.05)


@login_required
def video_feed(request, mode):
    ticket = _camera_ticket(request)
    if ticket is None:
        # بدون نشست: به‌جای تصویر زندهٔ کاربر دیگر، فقط تصویر ثابت
        return StreamingHttpResponse(
            gen_frames(ticket=None),
            content_type='multipart/x-mixed-replace; boundary=frame')
    return StreamingHttpResponse(
        gen_frames(ticket=ticket),
        content_type='multipart/x-mixed-replace; boundary=frame')


@login_required
def attendance_status(request):
    # اسکن کامل جدول Time روی هر پول‌زدن (هر ۱ ثانیه به‌ازای هر کاربر) زیر
    # بار دو کاربر هم‌زمان واقعاً سرور را زمین می‌زد؛ حالا محدود به یک‌بار در دقیقه.
    auto_fix_missing_checkouts_throttled()
    ticket = _camera_ticket(request)
    status = camera_manager.get_status(ticket=ticket)
    queue = camera_manager.queue_snapshot(request.user.username, _session_key(request))
    status['queue'] = queue
    status['my_ticket'] = ticket
    from users.models import ExitReason
    reasons = ExitReason.objects.filter(is_active=True).order_by('sort_order', 'title')
    exit_reasons = [
        {'id': r.id, 'title': r.title, 'is_system': r.is_system}
        for r in reasons
    ]
    if not any(r['is_system'] for r in exit_reasons):
        exit_reasons.append({'id': 'other', 'title': 'سایر موارد', 'is_system': True})
    status['exit_reasons'] = exit_reasons
    return JsonResponse(status)


@login_required
def submit_exit_reason(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)
    ticket = _camera_ticket(request)
    if ticket is None:
        return JsonResponse({'success': False, 'message': 'نشست معتبری برای شما یافت نشد.'})
    reason_id = request.POST.get('reason_id')
    reason_text = request.POST.get('reason_text', '')
    success, msg = camera_manager.confirm_exit_reason(ticket, reason_id, reason_text)
    return JsonResponse({'success': success, 'message': msg})


@login_required
def view_exit_reasons(request):
    if not can_access_admin(request):
        return redirect('not-authorised')
    from users.models import ExitReason
    reasons = ExitReason.objects.all().order_by('sort_order', 'title')
    return render(request, 'recognition/manage_exit_reasons.html', {'reasons': reasons})


@login_required
def add_exit_reason(request):
    if not can_access_admin(request):
        return JsonResponse({'success': False}, status=403)
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)
    from users.models import ExitReason
    title = request.POST.get('title', '').strip()
    if not title:
        return JsonResponse({'success': False, 'error': 'عنوان الزامی است'})
    max_order = ExitReason.objects.aggregate(models.Max('sort_order'))['sort_order__max'] or 0
    ExitReason.objects.create(title=title, is_active=True, sort_order=max_order + 10, is_system=False)
    return JsonResponse({'success': True})


@login_required
def toggle_exit_reason(request, reason_id):
    if not can_access_admin(request):
        return JsonResponse({'success': False}, status=403)
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)
    from users.models import ExitReason
    try:
        reason = ExitReason.objects.get(id=reason_id)
        if reason.is_system:
            return JsonResponse({'success': False, 'error': 'نمی‌توان دلیل سیستمی را تغییر داد'})
        reason.is_active = not reason.is_active
        reason.save()
        return JsonResponse({'success': True, 'is_active': reason.is_active})
    except ExitReason.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'یافت نشد'})


@login_required
def delete_exit_reason(request, reason_id):
    if not can_access_admin(request):
        return JsonResponse({'success': False}, status=403)
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)
    from users.models import ExitReason
    try:
        reason = ExitReason.objects.get(id=reason_id)
        if reason.is_system:
            return JsonResponse({'success': False, 'error': 'نمی‌توان دلیل سیستمی را حذف کرد'})
        reason.delete()
        return JsonResponse({'success': True})
    except ExitReason.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'یافت نشد'})


@login_required
def update_exit_reason_order(request, reason_id):
    if not can_access_admin(request):
        return JsonResponse({'success': False}, status=403)
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)
    from users.models import ExitReason
    import json
    try:
        data = json.loads(request.body)
        sort_order = int(data.get('sort_order', 0))
        reason = ExitReason.objects.get(id=reason_id)
        if reason.is_system:
            return JsonResponse({'success': False, 'error': 'نمی‌توان ترتیب دلیل سیستمی را تغییر داد'})
        reason.sort_order = sort_order
        reason.save(update_fields=['sort_order'])
        return JsonResponse({'success': True, 'sort_order': reason.sort_order})
    except ExitReason.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'یافت نشد'})
    except (ValueError, TypeError, json.JSONDecodeError):
        return JsonResponse({'success': False, 'error': 'داده‌های نامعتبر'})


@login_required
def reorder_exit_reasons(request):
    if not can_access_admin(request):
        return JsonResponse({'success': False}, status=403)
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)
    from users.models import ExitReason
    import json
    try:
        data = json.loads(request.body)
        order = data.get('order', [])  # list of reason IDs in new order
        for index, reason_id in enumerate(order):
            ExitReason.objects.filter(id=reason_id).update(sort_order=index * 10)
        return JsonResponse({'success': True})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


CHECKOUT_RESOLVED_PER_PAGE = 15


@login_required
def view_checkout_errors(request):
    if not can_access_admin(request):
        return redirect('not-authorised')
    auto_fix_missing_checkouts_throttled()

    # ---- فیلتر (جستجو / وضعیت / بازهٔ تاریخ شمسی) ----
    filter_form = CheckoutErrorFilterForm(request.GET)
    qs = (
        CheckOutError.objects
        .select_related('user', 'time_record', 'resolved_by')
        .all()
    )
    status_value = ''
    if filter_form.is_valid():
        data = filter_form.cleaned_data
        status_value = data.get('status') or ''
        q = (data.get('q') or '').strip()
        if q:
            qs = qs.filter(
                models.Q(user__username__icontains=q)
                | models.Q(user__first_name__icontains=q)
                | models.Q(user__last_name__icontains=q)
            )
        if data.get('date_from'):
            qs = qs.filter(date__gte=data['date_from'])
        if data.get('date_to'):
            qs = qs.filter(date__lte=data['date_to'])

    show_pending = status_value in ('', CheckoutErrorFilterForm.STATUS_PENDING)
    show_resolved = status_value in ('', CheckoutErrorFilterForm.STATUS_RESOLVED)

    pending_qs = qs.filter(status=CheckOutError.STATUS_PENDING)
    resolved_qs = qs.filter(status=CheckOutError.STATUS_RESOLVED)
    resolved_page = Paginator(
        resolved_qs, CHECKOUT_RESOLVED_PER_PAGE
    ).get_page(request.GET.get('resolved_page'))

    active_reasons = ExitReason.objects.filter(is_active=True).order_by('sort_order', 'title')

    return render(request, 'recognition/checkout_errors.html', {
        'filter_form': filter_form,
        'status_value': status_value,
        'is_filtered': any(
            request.GET.get(k)
            for k in ('q', 'status', 'date_from', 'date_to')
        ),
        'show_pending': show_pending,
        'show_resolved': show_resolved,
        'pending_errors': pending_qs if show_pending else CheckOutError.objects.none(),
        'pending_count': pending_qs.count(),
        'resolved_errors': resolved_page if show_resolved else None,
        'resolved_page': resolved_page if show_resolved else None,
        'resolved_count': resolved_qs.count(),
        'filter_query': urlencode(
            {k: v for k, v in request.GET.items()
             if k != 'resolved_page' and v},
        ),
        'active_reasons': active_reasons,
    })


@login_required
def resolve_checkout_error(request, error_id):
    if not can_access_admin(request):
        return redirect('not-authorised')
    error = get_object_or_404(CheckOutError, id=error_id)
    if request.method != 'POST':
        return redirect('view-checkout-errors')

    time_rec = error.time_record
    time_text = request.POST.get('out_time', '').strip()
    reason_id = (request.POST.get('exit_reason') or '').strip()
    reason_text = (request.POST.get('exit_reason_text') or '').strip()
    note = (request.POST.get('note') or '').strip()

    new_time_str = time_text or error.auto_out_time.strftime('%Y-%m-%dT%H:%M:%S')
    try:
        new_time = datetime.datetime.fromisoformat(new_time_str)
        if timezone.is_naive(new_time):
            new_time = timezone.make_aware(new_time)
    except (TypeError, ValueError):
        messages.error(request, 'زمان خروج نامعتبر است.')
        return redirect('view-checkout-errors')

    reason_obj = None
    if reason_id and reason_id != 'other':
        try:
            reason_obj = ExitReason.objects.get(id=reason_id)
        except ExitReason.DoesNotExist:
            pass
    elif reason_id == 'other':
        reason_text = reason_text or 'سایر موارد'

    time_rec.time = new_time
    time_rec.exit_reason = reason_obj
    time_rec.exit_reason_text = reason_text
    time_rec.save(update_fields=['time', 'exit_reason', 'exit_reason_text'])

    error.status = CheckOutError.STATUS_RESOLVED
    error.note = note
    error.resolved_by = request.user
    error.resolved_at = timezone.now()
    error.save(update_fields=['status', 'note', 'resolved_by', 'resolved_at'])

    messages.success(request, f'خطای خروج کارمند «{error.user.username}» با موفقیت رفع شد.')
    return redirect('view-checkout-errors')


@login_required
@require_POST
def reopen_checkout_error(request, error_id):
    """بازگشایی خطای رفع‌شده.

    وضعیت به «در انتظار بررسی» برمی‌گردد و زمان خروج ثبت‌شده به حالت
    خودکار (۲۳:۵۹:۵۹) بازگردانده می‌شود تا اصلاحِ دوباره انجام شود.
    """
    if not can_access_admin(request):
        return redirect('not-authorised')
    error = get_object_or_404(CheckOutError, id=error_id)

    if error.status != CheckOutError.STATUS_RESOLVED:
        messages.warning(request, 'فقط خطای «رفع‌شده» را می‌توان بازگشایی کرد.')
        return redirect('view-checkout-errors')

    error.status = CheckOutError.STATUS_PENDING
    error.resolved_by = None
    error.resolved_at = None
    error.save(update_fields=['status', 'resolved_by', 'resolved_at'])

    if error.time_record_id and error.auto_out_time:
        time_rec = error.time_record
        time_rec.time = error.auto_out_time
        time_rec.save(update_fields=['time'])

    messages.info(
        request,
        f'خطای خروج «{error.user.username}» بازگشایی شد و دوباره در انتظار بررسی است.',
    )
    return redirect('view-checkout-errors')


@login_required
def dataset_status(request):
    if not can_access_admin(request):
        return JsonResponse({'error': 'unauthorized'}, status=403)
    ticket = _camera_ticket(request, kind=KIND_DATASET)
    status = camera_manager.get_dataset_status(ticket=ticket)
    status['queue'] = camera_manager.queue_snapshot(
        request.user.username, _session_key(request))
    status['my_ticket'] = ticket
    return JsonResponse(status)


@login_required
def mark_break_out(request):
    if request.method == 'POST':
        form = BreakForm(request.POST)
        if form.is_valid():
            return _start_or_queue(
                request, KIND_BREAK_OUT, 'recognition/mark_break_stream.html',
                reason=form.cleaned_data['reason'])
    return redirect('dashboard')


@login_required
def mark_break_in(request):
    if request.method == 'POST':
        return _start_or_queue(
            request, KIND_BREAK_IN, 'recognition/mark_break_stream.html')
    return redirect('dashboard')


@login_required
def break_status(request):
    ticket = _camera_ticket(request)
    status = camera_manager.get_status(ticket=ticket)
    status['queue'] = camera_manager.queue_snapshot(
        request.user.username, _session_key(request))
    status['my_ticket'] = ticket
    return JsonResponse(status)


def current_time_api(request):
    now = timezone.localtime(timezone.now())
    # Convert to Jalali
    import jdatetime
    jdate = jdatetime.date.fromgregorian(date=now.date())
    jalali_months = ['فروردین', 'اردیبهشت', 'خرداد', 'تیر', 'مرداد', 'شهریور', 'مهر', 'آبان', 'آذر', 'دی', 'بهمن', 'اسفند']
    # jdatetime weekday: 0=Saturday, 1=Sunday, ..., 6=Friday
    jalali_weekdays = ['شنبه', 'یکشنبه', 'دوشنبه', 'سه‌شنبه', 'چهارشنبه', 'پنج‌شنبه', 'جمعه']
    weekday = jalali_weekdays[jdate.weekday()]
    fa_digits = str.maketrans('0123456789', '۰۱۲۳۴۵۶۷۸۹')
    jalali_str = f'{weekday}، {jdate.day} {jalali_months[jdate.month-1]} {jdate.year}'.translate(fa_digits)
    return JsonResponse({
        'time': now.strftime('%H:%M:%S'),
        'date': now.strftime('%Y-%m-%d'),
        'datetime': now.isoformat(),
        'jalali_date': jalali_str,
    })
