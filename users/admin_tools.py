"""ابزار «تغییر نام کاربری» در پنل ادمین.

چرا این صفحه لازم است
======================
نام کاربری روی مدل تشخیص چهره اثری ندارد (عکس‌ها و برچسب‌ها با شناسهٔ دائمی
`face_uid` نگه‌داری می‌شوند)، ولی یک نکته هست: بعضی جاهای قدیمی ممکن است نام
کاربری را ذخیره کرده باشند. این صفحه قبل از تغییر نام، وضعیت را نشان می‌دهد و
بعد از تغییر هم یک گزارش دقیق می‌دهد.

همچنین امکان «تغییر نام دسته‌ای» را می‌دهد: چند خط
``نام_قدیمی=نام_جدید`` می‌نویسی و یک‌جا اعمال می‌شود.
"""
from django.contrib import admin, messages
from django.contrib.auth.models import User
from django.shortcuts import render
from django.urls import path, reverse

from .faceid import face_uid_of, is_face_uid, dataset_dir_for_username
import os

CHANGE_USERNAME_URL = 'user-tools-rename'


def _dataset_state(user):
    """وضعیت عکس‌های ثبت‌شدهٔ این کاربر."""
    path = dataset_dir_for_username(user.username)
    if not path or not os.path.isdir(path):
        return None, 0
    try:
        count = len([f for f in os.listdir(path)
                     if f.lower().endswith(('.jpg', '.jpeg', '.png'))])
    except OSError:
        count = 0
    return path, count


def user_tools_view(request):
    """صفحهٔ تغییر نام کاربری."""
    if not request.user.is_superuser:
        messages.error(request, 'دسترسی ندارید.')
        return admin.site.index(request)

    result = None
    if request.method == 'POST':
        lines = (request.POST.get('mapping') or '').splitlines()
        pairs = []
        errors = []
        for raw in lines:
            raw = raw.strip()
            if not raw or raw.startswith('#'):
                continue
            if '=' not in raw:
                errors.append('قالب اشتباه: %s' % raw)
                continue
            old, new = (p.strip() for p in raw.split('=', 1))
            if not old or not new:
                errors.append('نام خالی: %s' % raw)
                continue
            pairs.append((old, new))

        if not pairs and not errors:
            errors.append('هیچ خطی برای تغییر نوشته نشد.')

        done = []
        for old, new in pairs:
            user = User.objects.filter(username=old).first()
            if user is None:
                errors.append('کاربری با نام %r پیدا نشد.' % old)
                continue
            if not new:
                errors.append('نام جدیدِ %r خالی است؛ تغییر نکرد.' % old)
                continue
            if User.objects.filter(username=new).exclude(pk=user.pk).exists():
                errors.append('نام %r قبلاً برای کاربر دیگری هست؛ %r تغییر نکرد.'
                              % (new, old))
                continue
            uid = face_uid_of(user.username)
            user.username = new
            user.save(update_fields=['username'])
            done.append((old, new, uid))

        if done:
            messages.success(
                request,
                '%d نام کاربری تغییر کرد. مدل و عکس‌ها دست‌نخورده ماندند.'
                % len(done))
        for err in errors:
            messages.error(request, err)
        result = {'done': done, 'errors': errors}

    # فهرست کاربران همراه با وضعیت عکس و شناسه
    rows = []
    for user in User.objects.select_related('profile').order_by('username'):
        path, count = _dataset_state(user)
        rows.append({
            'user': user,
            'uid': face_uid_of(user.username) or '—',
            'photo_count': count,
            'photo_path': path or '—',
            'has_photos': count > 0,
        })

    ctx = {
        **admin.site.each_context(request),
        'title': 'ابزار تغییر نام کاربری',
        'rows': rows,
        'result': result,
    }
    return render(request, 'admin/user_tools_rename.html', ctx)


def get_urls():
    """فقط مسیرهای ابزار را برمی‌گرداند (پیشوند admin/).

    این функция توسط ``KianAdminSite.get_urls`` صدا زده می‌شود
    و فقط مسیرهای Device Tools را اضافه می‌کند بدون آنکه مسیرهای
    ادمین عادی راperturb کند.
    """
    return [
        path('tools/rename-users/',
             admin.site.admin_view(user_tools_view),
             name=CHANGE_USERNAME_URL),
    ]