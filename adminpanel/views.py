"""ابزارهای پنل ادمین — همه زیر /admin/tools/ و فقط برای superuser.

هر صفحه بعد از عملیات POST با الگوی PRG برمی‌گردد تا رفرش مرورگر
عملیات را دوباره اجرا نکند، مگر آن‌هایی که باید نتیجه (مثل رمز عبور
تازه) را نشان دهند.
"""
import datetime as dt
import os

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.db import transaction
from django.http import FileResponse, Http404, HttpResponseRedirect
from django.shortcuts import render
from django.urls import path, reverse
from django.utils import timezone

from recognition.reports import fa_digits, jalali_full
from users.faceid import ensure_face_uid
from users.models import AttendanceSnapshot, Break, Present, Time, UserProfile
from users.snapshots import abs_path_for, snapshot_limit, snapshots_root

from . import dbtools, diagnostics, logtools
from .forms import (
    AttendanceFixForm,
    BackupLabelForm,
    BulkUserCreateForm,
    RestoreUploadForm,
    SqlForm,
)
from .utils import get_valid_username, random_password

User = get_user_model()


def _ctx(site, request, title, **extra):
    context = {**site.each_context(request), 'title': title, **extra}
    request.current_app = site.name
    return context


def _redirect(request, name):
    return HttpResponseRedirect(reverse('admin:%s' % name))


# ============================================================
# سلامت و تعمیر سیستم
# ============================================================
def health_view(site, request):
    if request.method == 'POST':
        if request.POST.get('all'):
            done, failed = diagnostics.fix_all()
            for msg in done:
                messages.success(request, msg)
            for msg in failed:
                messages.error(request, msg)
            if not done and not failed:
                messages.warning(request, 'چیزی برای رفع پیدا نشد.')
        else:
            ok, msg = diagnostics.fix_check(request.POST.get('key', ''))
            (messages.success if ok else messages.error)(request, msg)
        return _redirect(request, 'adminpanel-health')

    items = diagnostics.all_checks()
    problems = [i for i in items if i['status'] != 'ok']
    return render(request, 'adminpanel/health.html', _ctx(
        site, request, 'سلامت و تعمیر سیستم',
        items=items,
        problems=problems,
        fixable=sum(1 for i in problems if i['fixable']),
        errors=sum(1 for i in items if i['status'] == 'error'),
        warns=sum(1 for i in items if i['status'] == 'warn'),
        db_path=dbtools.db_path() or '—',
        db_size=_size_fa(dbtools.db_path()),
    ))


def _size_fa(path):
    if not path or not os.path.isfile(path):
        return '—'
    n = os.path.getsize(path)
    for unit in ('بایت', 'کیلوبایت', 'مگابایت', 'گیگابایت'):
        if n < 1024 or unit == 'گیگابایت':
            return '%s %s' % (int(n) if unit == 'بایت' else fa_digits('%.1f' % n),
                              unit)
        n /= 1024.0


# ============================================================
# پشتیبان‌گیری و بازیابی
# ============================================================
def backup_view(site, request):
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'create':
            form = BackupLabelForm(request.POST)
            if form.is_valid():
                name, msg = dbtools.create_backup(form.cleaned_data['label'])
                if name:
                    messages.success(request, msg + ' فایل: ' + name)
                else:
                    messages.error(request, msg)
            else:
                messages.error(request, 'برچسب نامعتبر است.')
        elif action == 'delete':
            ok, msg = dbtools.delete_backup(request.POST.get('name', ''))
            (messages.success if ok else messages.error)(request, msg)
        return _redirect(request, 'adminpanel-backup')

    return render(request, 'adminpanel/backup.html', _ctx(
        site, request, 'پشتیبان‌گیری و بازیابی دیتابیس',
        backups=dbtools.list_backups(),
        label_form=BackupLabelForm(),
        restore_form=RestoreUploadForm(),
        db_size=_size_fa(dbtools.db_path()),
        db_path=dbtools.db_path() or '—',
    ))


def backup_download_view(site, request, name):
    full = dbtools.open_backup(name)
    if not full:
        raise Http404('پشتیبان پیدا نشد')
    return FileResponse(open(full, 'rb'), as_attachment=True, filename=name)


def backup_restore_view(site, request, name):
    if request.method != 'POST':
        return _redirect(request, 'adminpanel-backup')
    ok, msg = dbtools.restore_backup(name)
    (messages.success if ok else messages.error)(request, msg)
    return _redirect(request, 'adminpanel-backup')


def backup_upload_view(site, request):
    if request.method != 'POST':
        return _redirect(request, 'adminpanel-backup')
    form = RestoreUploadForm(request.POST, request.FILES)
    if not form.is_valid():
        for err in form.errors.values():
            messages.error(request, '، '.join(err))
        return _redirect(request, 'adminpanel-backup')
    # فایل آپلود‌شده اول به پوشهٔ پشتیبان می‌رود، بعد بازیابی می‌شود
    filename = 'uploaded-%s.db' % timezone.now().strftime('%Y%m%d-%H%M%S')
    dest = os.path.join(dbtools.backup_dir(), filename)
    try:
        with open(dest, 'wb') as out:
            for chunk in form.cleaned_data['file'].chunks():
                out.write(chunk)
    except OSError as exc:
        messages.error(request, 'خطا در ذخیرهٔ فایل: %s' % exc)
        return _redirect(request, 'adminpanel-backup')
    ok, msg = dbtools.restore_backup(filename)
    (messages.success if ok else messages.error)(request, msg)
    return _redirect(request, 'adminpanel-backup')


# ============================================================
# کاوشگر SQL
# ============================================================
def sql_view(site, request):
    preview = None
    preview_table_name = ''
    result = None
    tables = dbtools.table_stats()

    if request.method == 'POST':
        if request.POST.get('preview_table'):
            preview_table_name = request.POST.get('preview_table', '')
            preview = dbtools.preview_table(preview_table_name)
            form = SqlForm(initial={'sql': request.POST.get('sql', '')})
        else:
            form = SqlForm(request.POST)
            if form.is_valid():
                result = dbtools.run_sql(
                    form.cleaned_data['sql'],
                    write=form.cleaned_data['write'],
                )
            else:
                result = {'ok': False, 'error': '، '.join(
                    sum(form.errors.values(), []))}
    else:
        form = SqlForm(initial={'sql': request.GET.get('q', '')})

    return render(request, 'adminpanel/sql.html', _ctx(
        site, request, 'کاوشگر دیتابیس (SQL)',
        form=form,
        result=result,
        preview=preview,
        preview_table=preview_table_name,
        tables=tables,
        write_allowed=bool(request.POST.get('write')),
    ))


# ============================================================
# لاگ سیستم
# ============================================================
def logs_view(site, request):
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'clear':
            logtools.clear()
            messages.success(request, 'فایل لاگ خالی شد.')
        return _redirect(request, 'adminpanel-logs')

    lines = request.GET.get('lines', '400')
    level = request.GET.get('level', '')
    try:
        lines = int(lines)
    except (TypeError, ValueError):
        lines = 400
    rows = logtools.tail(lines=lines, level=level)

    if request.GET.get('download'):
        response = FileResponse(
            iter([('\n'.join(rows)).encode('utf-8')]),
            content_type='text/plain; charset=utf-8',
        )
        response['Content-Disposition'] = 'attachment; filename="django.log"'
        return response

    return render(request, 'adminpanel/logs.html', _ctx(
        site, request, 'لاگ سیستم',
        rows=rows,
        total=len(rows),
        level=level,
        lines=lines,
        levels=logtools.LEVELS,
        counts=logtools.stats(rows),
        info=logtools.info(),
    ))


# ============================================================
# ایجاد گروهی کاربران
# ============================================================
def users_create_view(site, request):
    result = None
    form = BulkUserCreateForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        created, existing, errors = [], [], []
        for row in form.cleaned_data['lines']:
            username = get_valid_username(row['username'])
            if User.objects.filter(username=username).exists():
                existing.append(username)
                continue
            password = (row['password']
                        or form.cleaned_data['default_password']
                        or random_password(12))
            try:
                with transaction.atomic():
                    user = User.objects.create_user(
                        username=username,
                        password=password,
                        first_name=row['first'],
                        last_name=row['last'],
                        email=row['email'],
                    )
                    role = row['role'] or form.cleaned_data['default_role']
                    if role not in ('user', 'admin', 'both'):
                        role = 'user'
                    profile, _ = UserProfile.objects.get_or_create(user=user)
                    profile.role = role
                    profile.save(update_fields=['role'])
                    if form.cleaned_data['make_staff'] and role in ('admin', 'both'):
                        user.is_staff = True
                        user.save(update_fields=['is_staff'])
                    if form.cleaned_data['ensure_face']:
                        ensure_face_uid(user)
            except Exception as exc:
                errors.append('%s: %s' % (username, exc))
                continue
            created.append({'username': username, 'password': password,
                            'role': role})
        result = {'created': created, 'existing': existing, 'errors': errors}

    return render(request, 'adminpanel/users_create.html', _ctx(
        site, request, 'ایجاد گروهی کاربران',
        form=form, result=result,
    ))


# ============================================================
# عملیات گروهی روی کاربران
# ============================================================
def _ids_from_request(request):
    raw = request.POST.get('ids') or request.GET.get('ids') or ''
    return [int(x) for x in raw.replace(' ', '').split(',') if x.isdigit()]


def users_bulk_view(site, request):
    ids = _ids_from_request(request)
    users = list(
        User.objects.filter(pk__in=ids).select_related('profile').order_by('username')
    ) if ids else []
    result = None

    if request.method == 'POST' and users:
        action = request.POST.get('action', '')
        result = _apply_bulk_action(request, users, action)
        # بعد از عملیات، وضعیت تازه نمایش داده می‌شود
        users = list(
            User.objects.filter(pk__in=ids).select_related('profile')
            .order_by('username')
        )

    all_users = User.objects.select_related('profile').order_by('username')
    return render(request, 'adminpanel/users_bulk.html', _ctx(
        site, request, 'عملیات گروهی کاربران',
        users=users,
        ids=','.join(str(u.pk) for u in users),
        result=result,
        all_users=all_users[:300],
    ))


def _apply_bulk_action(request, users, action):
    from django.contrib.auth.hashers import make_password

    changed, skipped, passwords, errors = [], [], [], []
    me = request.user

    for user in users:
        try:
            if action == 'activate':
                if not user.is_active:
                    user.is_active = True
                    user.save(update_fields=['is_active'])
                changed.append(user.username)
            elif action == 'deactivate':
                if user.pk == me.pk:
                    skipped.append('%s (خودتان)' % user.username)
                    continue
                user.is_active = False
                user.save(update_fields=['is_active'])
                changed.append(user.username)
            elif action in ('role_user', 'role_admin', 'role_both'):
                if user.is_superuser:
                    skipped.append('%s (superuser نقش مجزا ندارد)' % user.username)
                    continue
                role = action.split('_', 1)[1]
                profile, _ = UserProfile.objects.get_or_create(user=user)
                profile.role = role
                profile.save(update_fields=['role'])
                changed.append(user.username)
            elif action == 'make_staff':
                if not user.is_staff:
                    user.is_staff = True
                    user.save(update_fields=['is_staff'])
                changed.append(user.username)
            elif action == 'drop_staff':
                if user.pk == me.pk:
                    skipped.append('%s (خودتان)' % user.username)
                    continue
                user.is_staff = False
                user.save(update_fields=['is_staff'])
                changed.append(user.username)
            elif action == 'make_superuser':
                if not user.is_superuser:
                    user.is_superuser = True
                    user.is_staff = True
                    user.save(update_fields=['is_superuser', 'is_staff'])
                changed.append(user.username)
            elif action == 'drop_superuser':
                if user.pk == me.pk:
                    skipped.append('%s (خودتان)' % user.username)
                    continue
                if user.is_superuser:
                    user.is_superuser = False
                    user.save(update_fields=['is_superuser'])
                changed.append(user.username)
            elif action == 'reset_password':
                password = (request.POST.get('password') or '').strip()
                new_password = password or random_password(12)
                if password:
                    user.password = make_password(password)
                    user.save(update_fields=['password'])
                else:
                    user.set_password(new_password)
                    user.save(update_fields=['password'])
                passwords.append((user.username, new_password))
                changed.append(user.username)
            elif action == 'sync_profile':
                profile, _ = UserProfile.objects.get_or_create(user=user)
                if not profile.face_uid:
                    ensure_face_uid(user)
                changed.append(user.username)
            elif action == 'force_logout':
                n = _flush_user_sessions(user)
                changed.append('%s (%s نشست)' % (user.username, fa_digits(n)))
            elif action == 'delete':
                if user.pk == me.pk:
                    skipped.append('%s (خودتان)' % user.username)
                    continue
                user.delete()
                changed.append(user.username)
            else:
                errors.append('عملیات ناشناخته: %s' % action)
        except Exception as exc:
            errors.append('%s: %s' % (user.username, exc))

    if changed:
        messages.success(request, 'عملیات روی %s کاربر اعمال شد.'
                         % fa_digits(len(changed)))
    if skipped:
        messages.warning(request, 'نادیده گرفته شد: ' + '، '.join(skipped))
    for err in errors:
        messages.error(request, err)

    return {'changed': changed, 'skipped': skipped,
            'passwords': passwords, 'errors': errors}


def _flush_user_sessions(user):
    """همهٔ نشست‌های این کاربر را باطل می‌کند."""
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
# ترمیم حضور و غیاب
# ============================================================
def attendance_fix_view(site, request):
    form = AttendanceFixForm(request.POST or None)
    day_view = None
    date_value = request.GET.get('date') or request.POST.get('date')
    if request.method == 'POST' and form.is_valid():
        action = form.cleaned_data['action']
        minutes = form.cleaned_data.get('minutes') or 30
        ok, msg = diagnostics.fix_check(action, minutes=minutes)
        (messages.success if ok else messages.error)(request, msg)
        return _redirect(request, 'adminpanel-attendance')

    if date_value:
        try:
            day = dt.date.fromisoformat(date_value)
        except ValueError:
            day = None
        if day:
            day_view = _day_overview(day)

    today = timezone.localdate()
    day_view = day_view or _day_overview(today)
    return render(request, 'adminpanel/attendance.html', _ctx(
        site, request, 'ترمیم حضور و غیاب',
        form=form, day=day_view, today=today,
        pending_errors=diagnostics.check_pending_checkout_errors()[0],
    ))


def _day_overview(day):
    times = (Time.objects.filter(date=day)
             .select_related('user', 'exit_reason')
             .order_by('user__username', 'time'))
    presents = (Present.objects.filter(date=day)
                .select_related('user').order_by('user__username'))
    breaks = (Break.objects.filter(date=day)
              .select_related('user').order_by('user__username'))
    users = sorted({t.user for t in times} | {p.user for p in presents})
    return {
        'date': day,
        'jalali': jalali_full(day),
        'times': times,
        'presents': presents,
        'breaks': breaks,
        'users': users,
        'in_count': sum(1 for t in times if not t.out),
        'out_count': sum(1 for t in times if t.out),
        'present_count': sum(1 for p in presents if p.present),
        'time_url': '/admin/users/time/?date__exact=%s' % day,
        'present_url': '/admin/users/present/?date__exact=%s' % day,
        'break_url': '/admin/users/break/?date__exact=%s' % day,
    }


# ============================================================
# پاک‌سازی عکس‌های چهره و لحظه
# ============================================================
def snapshots_tool_view(site, request):
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'fix_missing':
            ok, msg = diagnostics.fix_check('snapshot_missing_files')
            (messages.success if ok else messages.error)(request, msg)
        elif action == 'fix_orphans':
            ok, msg = diagnostics.fix_check('orphan_snapshot_files')
            (messages.success if ok else messages.error)(request, msg)
        elif action == 'purge':
            days = int(request.POST.get('days') or 0)
            n, msg = _purge_snapshots(days=days)
            messages.success(request, msg)
        elif action == 'purge_user':
            username = request.POST.get('username', '')
            n, msg = _purge_user_snapshots(username)
            (messages.success if n or msg else messages.warning)(request, msg)
        return _redirect(request, 'adminpanel-snapshots')

    from django.db.models import Count
    per_user = (
        User.objects.filter(attendance_snapshots__isnull=False)
        .annotate(n=Count('attendance_snapshots'))
        .select_related('profile')
        .order_by('-n')
    )
    missing = diagnostics.check_snapshot_missing_files()
    orphans = diagnostics.check_orphan_snapshot_files()
    face_root = diagnostics.training_root()

    return render(request, 'adminpanel/snapshots.html', _ctx(
        site, request, 'پاک‌سازی عکس‌های چهره',
        per_user=per_user,
        limit=snapshot_limit(),
        snapshots_root=snapshots_root(),
        face_root=face_root,
        missing=missing, orphans=orphans,
        face_dirs=_face_dir_stats(),
        users_without_photos=diagnostics.check_users_without_photos(),
    ))


def _face_dir_stats():
    counts = diagnostics.photo_counts(refresh=True)
    total = sum(counts.values())
    return {'folders': len(counts), 'photos': total,
            'photos_fa': fa_digits(total)}


def _purge_snapshots(days=0):
    root = snapshots_root()
    cutoff = timezone.now() - dt.timedelta(days=max(days, 0))
    qs = AttendanceSnapshot.objects.all()
    if days:
        qs = qs.filter(created_at__lt=cutoff)
    removed = 0
    for snap in qs:
        path = abs_path_for(snap)
        if path:
            try:
                os.remove(path)
            except OSError:
                pass
        snap.delete()
        removed += 1
    diagnostics.invalidate_photo_cache()
    return removed, '%s عکس حذف شد.' % fa_digits(removed)


def _purge_user_snapshots(username):
    if not username:
        return 0, 'کاربری انتخاب نشده.'
    qs = AttendanceSnapshot.objects.filter(user__username=username)
    n = qs.count()
    for snap in qs:
        path = abs_path_for(snap)
        if path:
            try:
                os.remove(path)
            except OSError:
                pass
        snap.delete()
    return n, '%s عکس برای «%s» حذف شد.' % (fa_digits(n), username)


# ============================================================
# مدیریت نشست‌ها
# ============================================================
def sessions_tool_view(site, request):
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'expired':
            n, _ = Session.objects.filter(
                expire_date__lt=timezone.now()).delete()
            messages.success(request, '%s نشست منقضی پاک شد.'
                             % fa_digits(n))
        elif action == 'all':
            n, _ = Session.objects.all().delete()
            messages.success(request, '%s نشست پاک شد (همهٔ کاربران '
                             'دوباره وارد می‌شوند).' % fa_digits(n))
        elif action == 'user':
            username = request.POST.get('username', '')
            user = User.objects.filter(username=username).first()
            if user:
                n = _flush_user_sessions(user)
                messages.success(request, '%s نشست برای «%s» پاک شد.'
                                 % (fa_digits(n), username))
            else:
                messages.error(request, 'کاربر پیدا نشد.')
        return _redirect(request, 'adminpanel-sessions')

    now = timezone.now()
    active = []
    for session in Session.objects.filter(expire_date__gte=now)[:200]:
        data = session.get_decoded()
        uid = data.get('_auth_user_id')
        username = ''
        if uid:
            username = (User.objects.filter(pk=uid)
                        .values_list('username', flat=True).first()) or '—'
        active.append({
            'key': session.session_key[:10] + '…',
            'username': username or '—',
            'expire': timezone.localtime(session.expire_date),
        })
    expired = Session.objects.filter(expire_date__lt=now).count()

    return render(request, 'adminpanel/sessions.html', _ctx(
        site, request, 'مدیریت نشست‌ها',
        active=active,
        total=Session.objects.count(),
        expired=expired,
        users=User.objects.order_by('username'),
    ))


# ============================================================
# تغییر نام کاربری (نسخهٔ قبلی، حالا با نام یکسان)
# ============================================================
def rename_view(site, request):
    from users.admin_tools import user_tools_view
    return user_tools_view(request)


# ============================================================
# ثبت مسیرها
# ============================================================
def tool_urls(site):
    v = site.admin_view
    return [
        path('tools/health/', v(lambda r: health_view(site, r)),
             name='adminpanel-health'),
        path('tools/backup/', v(lambda r: backup_view(site, r)),
             name='adminpanel-backup'),
        path('tools/backup/download/<str:name>/',
             v(lambda r, name: backup_download_view(site, r, name)),
             name='adminpanel-backup-download'),
        path('tools/backup/restore/<str:name>/',
             v(lambda r, name: backup_restore_view(site, r, name)),
             name='adminpanel-backup-restore'),
        path('tools/backup/upload/', v(lambda r: backup_upload_view(site, r)),
             name='adminpanel-backup-upload'),
        path('tools/sql/', v(lambda r: sql_view(site, r)),
             name='adminpanel-sql'),
        path('tools/logs/', v(lambda r: logs_view(site, r)),
             name='adminpanel-logs'),
        path('tools/users/create/', v(lambda r: users_create_view(site, r)),
             name='adminpanel-users-create'),
        path('tools/users/bulk/', v(lambda r: users_bulk_view(site, r)),
             name='adminpanel-users-bulk'),
        path('tools/attendance/', v(lambda r: attendance_fix_view(site, r)),
             name='adminpanel-attendance'),
        path('tools/snapshots/', v(lambda r: snapshots_tool_view(site, r)),
             name='adminpanel-snapshots'),
        path('tools/sessions/', v(lambda r: sessions_tool_view(site, r)),
             name='adminpanel-sessions'),
        path('tools/rename-users/', v(lambda r: rename_view(site, r)),
             name='adminpanel-rename'),
    ]
