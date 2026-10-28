import os
import shutil
from django.shortcuts import render, redirect
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib import messages
from django.contrib.auth import logout as auth_logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.http import JsonResponse
from django.db import transaction
from django.views.decorators.http import require_POST
from .forms import SmartRegisterForm, UserEditForm
from users.models import Present, Time, Break, CheckOutError
from .roles import (
    ROLE_USER, ROLE_ADMIN, ROLE_BOTH, VALID_ROLES,
    get_role, set_role, is_admin_user, can_access_admin,
)


TRAINING_DIR = 'face_recognition_data/training_dataset'


def _face_uid_of_user(user):
    """Get the face_uid for a user, with fallback to username-based lookup."""
    from users.models import UserProfile
    profile = UserProfile.objects.filter(user=user).first()
    if profile and profile.face_uid:
        return profile.face_uid
    return None


def get_face_status(user):
    # Try face_uid-based folder first (new system)
    uid = _face_uid_of_user(user)
    if uid:
        user_dir = os.path.join(TRAINING_DIR, uid)
        if os.path.isdir(user_dir):
            photos = [f for f in os.listdir(user_dir) if f.lower().endswith(('.jpg', '.jpeg', '.png'))]
            count = len(photos)
            if count > 0:
                gallery_path = 'face_recognition_data/emb_names.npy'
                is_trained = False
                if os.path.exists(gallery_path):
                    import numpy as np
                    try:
                        names = np.load(gallery_path, allow_pickle=True).tolist()
                        if uid in names:
                            is_trained = True
                    except Exception:
                        pass
                if is_trained:
                    return 'trained', count
                else:
                    return 'photos_only', count
            else:
                return 'none', 0
    # Fall back to username-based folder (old system, for backward compat)
    user_dir = os.path.join(TRAINING_DIR, user.username)
    if not os.path.isdir(user_dir):
        return 'none', 0
    photos = [f for f in os.listdir(user_dir) if f.lower().endswith(('.jpg', '.jpeg', '.png'))]
    count = len(photos)
    if count == 0:
        return 'none', 0
    gallery_path = 'face_recognition_data/emb_names.npy'
    if os.path.exists(gallery_path):
        import numpy as np
        try:
            names = np.load(gallery_path, allow_pickle=True).tolist()
            if user.username in names:
                return 'trained', count
        except Exception:
            pass
    return 'photos_only', count


def get_user_attendance_summary(user):
    present_count = Present.objects.filter(user=user, present=True).count()
    last_present = Present.objects.filter(user=user, present=True).order_by('-date').first()
    last_date = last_present.date if last_present else None
    return present_count, last_date

    last_date = last_present.date if last_present else None
    return present_count, last_date


@login_required
def register(request):
    if not can_access_admin(request):
        return redirect('not-authorised')
    if request.method == 'POST':
        form = SmartRegisterForm(request.POST)
        if form.is_valid():
            user = form.save()
            messages.success(request, f'کارمند {user.username} با موفقیت ثبت شد. حالا عکس‌های چهره را بگیرید.')
            return redirect('register-photo-capture', user_id=user.id)
    else:
        form = SmartRegisterForm()
    return render(request, 'users/register.html', {'form': form})


@login_required
def users_list(request):
    if not can_access_admin(request):
        return redirect('not-authorised')
    users = User.objects.filter(is_superuser=False).order_by('id')
    user_data = []
    for u in users:
        face_status, photo_count = get_face_status(u)
        attendance_count, last_date = get_user_attendance_summary(u)
        user_data.append({
            'user': u,
            'role': get_role(u),
            'face_status': face_status,
            'photo_count': photo_count,
            'attendance_count': attendance_count,
            'last_date': last_date,
        })
    return render(request, 'recognition/users_list.html', {'user_data': user_data})


@login_required
def user_edit(request, user_id):
    if not can_access_admin(request):
        return redirect('not-authorised')
    target_user = User.objects.get(id=user_id)
    face_status, photo_count = get_face_status(target_user)
    attendance_count, last_date = get_user_attendance_summary(target_user)
    target_user_role = get_role(target_user)

    if request.method == 'POST':
        action = request.POST.get('action', 'edit')
        if action == 'delete':
            if target_user.is_superuser:
                messages.error(request, 'امکان حذف مدیر وجود ندارد.')
                return redirect('users-list')
            user_dir = os.path.join(TRAINING_DIR, target_user.username)
            if os.path.isdir(user_dir):
                shutil.rmtree(user_dir)
            target_user.delete()
            messages.success(request, f'کارمند با موفقیت حذف شد.')
            return redirect('users-list')
        elif action == 'retake_photos':
            return redirect('register-photo-capture', user_id=target_user.id)
        elif action == 'clear_attendance':
            # Clear all attendance records for this user
            with transaction.atomic():
                Present.objects.filter(user=target_user).delete()
                Time.objects.filter(user=target_user).delete()
                Break.objects.filter(user=target_user).delete()
                CheckOutError.objects.filter(user=target_user).delete()
            messages.success(request, f'تمام سوابق حضور و غیاب کاربر {target_user.username} با موفقیت پاک شد.')
            return redirect('user-edit', user_id=target_user.id)
        else:
            form = UserEditForm(request.POST, instance=target_user)
            if form.is_valid():
                form.save()
                raw_role = request.POST.get('role', '')
                if raw_role in VALID_ROLES:
                    set_role(target_user, raw_role)
                    target_user_role = get_role(target_user)
                # Handle password change
                new_password = form.cleaned_data.get('new_password')
                if new_password:
                    target_user.set_password(new_password)
                    target_user.save()
                    messages.success(request, 'اطلاعات کارمند و رمز عبور با موفقیت بروزرسانی شد.')
                else:
                    messages.success(request, 'اطلاعات کارمند بروزرسانی شد.')
                return redirect('user-edit', user_id=target_user.id)
    else:
        form = UserEditForm(instance=target_user)

    return render(request, 'recognition/user_edit.html', {
        'form': form,
        'target_user': target_user,
        'target_user_role': target_user_role,
        'face_status': face_status,
        'photo_count': photo_count,
        'attendance_count': attendance_count,
        'last_date': last_date,
    })


@login_required
def user_delete(request, user_id):
    if not can_access_admin(request):
        return redirect('not-authorised')
    target_user = User.objects.get(id=user_id)
    if target_user.is_superuser:
        messages.error(request, 'امکان حذف مدیر وجود ندارد.')
        return redirect('users-list')
    user_dir = os.path.join(TRAINING_DIR, target_user.username)
    if os.path.isdir(user_dir):
        shutil.rmtree(user_dir)
    target_user.delete()
    messages.success(request, f'کارمند {target_user.username} حذف شد.')
    return redirect('users-list')


@login_required
def edit_profile(request):
    if request.method == 'POST':
        form = UserEditForm(request.POST, instance=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, 'پروفایل با موفقیت بروزرسانی شد.')
            return redirect('dashboard')
    else:
        form = UserEditForm(instance=request.user)
    return render(request, 'users/edit_profile.html', {'form': form})


@login_required
def change_password(request):
    if request.method == 'POST':
        form = PasswordChangeForm(request.user, request.POST)
        if form.is_valid():
            user = form.save()
            update_session_auth_hash(request, user)
            messages.success(request, 'رمز عبور با موفقیت تغییر کرد.')
            return redirect('dashboard')
    else:
        form = PasswordChangeForm(request.user)
    return render(request, 'users/change_password.html', {'form': form})


def custom_logout(request):
    auth_logout(request)
    return redirect('home')


@login_required
def admin_mode_on(request):
    """کاربر «both» وارد حالت ادمین می‌شود (همه‌چیز ادمین نمایش داده می‌شود)."""
    if get_role(request.user) == ROLE_BOTH:
        request.session['admin_mode'] = True
        messages.success(request, 'حالت ادمین فعال شد.')
    return redirect('dashboard')


@login_required
def admin_mode_off(request):
    """کاربر «both» از حالت ادمین خارج شده و به حالت کاربر عادی برمی‌گردد."""
    request.session['admin_mode'] = False
    messages.success(request, 'به حالت کاربر عادی برگشتید.')
    return redirect('dashboard')


# ============================================================
# بازبینی عکس‌های لحظهٔ ورود/خروج
# ============================================================
@login_required
def snapshot_review(request):
    """صفحهٔ بازبینی مدیر: فهرست کاربران دارای عکس + شبکه‌ای از عکس‌های یک کاربر."""
    if not can_access_admin(request):
        return redirect('not-authorised')

    from django.db.models import Count
    from users.models import AttendanceSnapshot
    from .snapshots import snapshot_limit

    username = (request.GET.get('username') or '').strip()
    selected = None
    snapshots = []
    total_count = 0
    missing_count = 0
    if username:
        try:
            selected = User.objects.get(username=username)
        except User.DoesNotExist:
            messages.warning(request, 'کاربر یافت نشد.')
            selected = None
    if selected:
        from .snapshots import abs_path_for
        qs = (
            AttendanceSnapshot.objects.filter(user=selected)
            .order_by('-created_at', '-id')
        )
        total_count = qs.count()
        # فقط رکوردهایی که فایلشان واقعاً روی دیسک هست نمایش داده می‌شوند.
        # اگر دیتابیس از بکاپ بازیابی شده باشد، ممکن است رکورد بماند ولی فایل
        # نداشته باشد؛ در این حالت نباید اولین کارت (جدیدترین) عکس شکسته باشد.
        snapshots = [snap for snap in qs if abs_path_for(snap)]
        missing_count = total_count - len(snapshots)

    users_rows = (
        User.objects.filter(attendance_snapshots__isnull=False)
        .annotate(photo_count=Count('attendance_snapshots'))
        .order_by('username')
        .only('username', 'first_name', 'last_name')
    )

    return render(request, 'recognition/snapshot_review.html', {
        'users_rows': users_rows,
        'selected': selected,
        'snapshots': snapshots,
        'total_count': total_count,
        'missing_count': missing_count,
        'limit': snapshot_limit(),
    })


@login_required
def snapshot_image(request, snapshot_id):
    """عکس یک رکورد را فقط برای مدیر برمی‌گرداند (با حفاظت مسیر)."""
    if not can_access_admin(request):
        return redirect('not-authorised')
    from django.http import FileResponse, HttpResponseNotFound
    from users.models import AttendanceSnapshot
    from .snapshots import abs_path_for
    try:
        snap = AttendanceSnapshot.objects.get(id=snapshot_id)
    except AttendanceSnapshot.DoesNotExist:
        return HttpResponseNotFound('not found')
    path = abs_path_for(snap)
    if not path:
        # فایل روی سرور نیست (مثلاً پس از بازیابی دیتابیس بدون فایل‌ها).
        # به‌جای تصویر شکسته، یک جای‌نگهدار خوانا برمی‌گردانیم.
        from django.http import HttpResponse
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="480" '
            'viewBox="0 0 640 480">'
            '<rect width="100%" height="100%" fill="#0f172a"/>'
            '<text x="50%" y="46%" fill="#94a3b8" font-size="28" '
            'text-anchor="middle" font-family="sans-serif">بدون عکس</text>'
            '<text x="50%" y="56%" fill="#64748b" font-size="17" '
            'text-anchor="middle" font-family="sans-serif">'
            'فایل این عکس روی سرور موجود نیست</text>'
            '</svg>'
        )
        return HttpResponse(svg, content_type='image/svg+xml')
    return FileResponse(open(path, 'rb'), content_type='image/jpeg')


@login_required
@require_POST
def snapshot_delete(request, snapshot_id):
    """حذف یک عکس و فایل آن (فقط مدیر)."""
    if not can_access_admin(request):
        return redirect('not-authorised')
    from users.models import AttendanceSnapshot
    from .snapshots import abs_path_for, snapshots_root
    import os
    try:
        snap = AttendanceSnapshot.objects.get(id=snapshot_id)
    except AttendanceSnapshot.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'یافت نشد'}, status=404)
    path = abs_path_for(snap)
    if path and os.path.isfile(path):
        os.remove(path)
    snap.delete()
    return JsonResponse({'success': True})
