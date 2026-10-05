"""ذخیرهٔ عکس لحظهٔ ورود/خروجِ تأییدشده.

هر بار که چهرهٔ یک کارمند در جریان ورود/خروج تأیید شد، فریم همان لحظه به‌صورت
JPEG زیر پوشهٔ SNAPSHOT_DIR ذخیره می‌شود و رکوردی در AttendanceSnapshot می‌سازیم.
برای هر کاربر فقط آخرین SNAPSHOT_LIMIT_PER_USER عکس نگه داشته می‌شود؛ به‌محض ورود
عکس جدید، قدیمی‌ترین عکس همان کاربر (و فایلش) حذف می‌شود.
"""
import os

import cv2
from django.conf import settings
from django.contrib.auth.models import User
from django.utils import timezone

from .models import AttendanceSnapshot
from .faceid import face_uid_of_user


def snapshots_root():
    return os.path.join(settings.BASE_DIR, 'face_recognition_data', 'snapshots')


def snapshot_limit():
    return getattr(settings, 'SNAPSHOT_LIMIT_PER_USER', 50)


def save_snapshot(username, frame_bgr, event):
    """فریم تأییدشده را ذخیره و رکورد می‌سازد.

    این تابع هرگز نباید استثنا پرتاب کند؛ نقص عکس نباید جریان ورود/خروج را
    مختل کند یا ترد دوربین را از بین ببرد.
    """
    try:
        user = User.objects.get(username=username)
    except User.DoesNotExist:
        return None
    if frame_bgr is None or not hasattr(frame_bgr, 'shape'):
        return None
    if event not in (AttendanceSnapshot.EVENT_IN, AttendanceSnapshot.EVENT_OUT):
        event = AttendanceSnapshot.EVENT_IN

    try:
        ok, buf = cv2.imencode('.jpg', frame_bgr, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not ok:
            return None

        now = timezone.now()
        stamp = now.strftime('%Y%m%d_%H%M%S_%f')
        # پوشه با شناسهٔ دائمی ساخته می‌شود تا تغییر نام کاربری تاریخچهٔ
        # عکس‌ها را از هم جدا نکند. مسیر در خودِ رکورد دیتابیس ذخیره می‌شود،
        # پس عکس‌های قدیمی (که پوشه‌شان نام کاربری بود) همچنان سالم می‌مانند.
        folder = face_uid_of_user(user) or username
        rel_path = os.path.join(folder, '%s_%s.jpg' % (stamp, event))
        abs_path = os.path.join(snapshots_root(), rel_path)
        abs_dir = os.path.dirname(abs_path)
        os.makedirs(abs_dir, exist_ok=True)
        with open(abs_path, 'wb') as f:
            f.write(buf.tobytes())

        snap = AttendanceSnapshot.objects.create(
            user=user,
            event=event,
            photo=rel_path,
            created_at=now,
        )
        _enforce_limit(user)
        return snap
    except Exception:
        return None


def _enforce_limit(user):
    """اگر تعداد عکس‌های کاربر از سقف گذشت، قدیمی‌ترین‌ها حذف می‌شوند."""
    limit = snapshot_limit()
    qs = AttendanceSnapshot.objects.filter(user=user).order_by('-created_at', '-id')
    if qs.count() <= limit:
        return
    for old in qs[limit:]:
        _delete_file(snapshots_root(), old.photo)
        old.delete()


def _delete_file(root, rel_path):
    try:
        p = os.path.realpath(os.path.join(root, rel_path))
        root_real = os.path.realpath(root)
        if p.startswith(root_real + os.sep) and os.path.isfile(p):
            os.remove(p)
    except Exception:
        pass


def abs_path_for(snap):
    """مسیر مطلق یک رکورد (با محافظت از محو مسیر)."""
    root_real = os.path.realpath(snapshots_root())
    p = os.path.realpath(os.path.join(root_real, snap.photo))
    if p.startswith(root_real + os.sep) and os.path.isfile(p):
        return p
    return None