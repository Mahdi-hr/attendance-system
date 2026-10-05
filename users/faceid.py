"""شناسهٔ داخلیِ ثابت برای هر کاربر — جدا از نام کاربری.

چرا این ماژول وجود دارد
=======================
مدل تشخیص چهره از نام کاربری به‌عنوان «برچسب» استفاده می‌کند و عکس‌های هر
کس در پوشه‌ای به نام خودش نگه‌داری می‌شود. یعنی اگر نام کاربری عوض شود و
پوشه جابه‌جا نشود، مدل دیگر آن فرد را نمی‌شناسد.

اینجا یک شناسهٔ دائمی می‌سازیم (`face_uid`) که:

* یک‌بار ساخته می‌شود و **هیچ‌وقت تغییر نمی‌کند**
* به کاربر نشان داده نمی‌شود و ربطی به نام نمایشی ندارد
* نام پوشهٔ عکس‌ها و برچسبِ مدل می‌شود

نتیجه: تغییر نام کاربری (مثلاً `toghiani` → `1010`) فقط یک فیلد دیتابیس را
عوض می‌کند و **هیچ اثری روی مدل، پوشه‌ها یا عکس‌ها ندارد**.

قرارداد نام پوشه
=================
پوشهٔ عکس‌های هر کس: ``face_recognition_data/training_dataset/<face_uid>/``

سازگاری با عقب
===============
پوشه‌های قدیمی که نامشان نام کاربری است همچنان خوانده می‌شوند
(`face_uid_for_label` آن‌ها را نگاشت می‌کند)، ولی `migrate_face_uids`
آن‌ها را به `face_uid` تبدیل می‌کند.
"""
import os
import uuid

from django.contrib.auth.models import User
from django.db import close_old_connections

from .models import UserProfile

# با حرف شروع می‌شود تا با نام کاربری اشتباه گرفته نشود و در همهٔ
# سیستم‌های فایل امن باشد. حروف کوچک تا در ویندوز/مک با مقایسهٔ
# غیرحساس به حروف هم سالم بماند.
FACE_UID_PREFIX = 'u'
FACE_UID_HEX_LEN = 12
FACE_UID_LEN = len(FACE_UID_PREFIX) + FACE_UID_HEX_LEN

TRAINING_DIR = os.path.join('face_recognition_data', 'training_dataset')


# ============================================================
# ساخت و خواندن شناسه
# ============================================================
def new_face_uid():
    """یک شناسهٔ تازه و یکتا می‌سازد."""
    return FACE_UID_PREFIX + uuid.uuid4().hex[:FACE_UID_HEX_LEN]


def is_face_uid(value):
    """آیا این رشته یک شناسهٔ داخلی معتبر است؟"""
    if not value or len(value) != FACE_UID_LEN:
        return False
    if not value.startswith(FACE_UID_PREFIX):
        return False
    return all(c in '0123456789abcdef' for c in value[len(FACE_UID_PREFIX):])


def ensure_profile(user):
    """پروفایل کاربر را می‌گیرد یا می‌سازد (بدون ذخیره)."""
    profile, _ = UserProfile.objects.get_or_create(user=user)
    return profile


def ensure_face_uid(user, save=True):
    """شناسهٔ کاربر را برمی‌گرداند و اگر ندارد می‌سازد.

    این تابع «یک‌باره» است: بعد از اولین فراخوانی، شناسه هرگز عوض نمی‌شود.
    """
    profile = ensure_profile(user)
    if profile.face_uid:
        return profile.face_uid
    # یکتا بودن را در سطح دیتابیس هم تضمین می‌کنیم
    for _ in range(20):
        candidate = new_face_uid()
        if not UserProfile.objects.filter(face_uid=candidate).exists():
            break
    else:
        raise RuntimeError('تولید شناسهٔ یکتا برای کاربر ممکن نشد')
    profile.face_uid = candidate
    if save:
        profile.save(update_fields=['face_uid'])
    return candidate


def face_uid_of(username):
    """شناسهٔ کاربر با این نام، یا None."""
    if not username:
        return None
    return (UserProfile.objects
            .filter(user__username=username)
            .exclude(face_uid='')
            .values_list('face_uid', flat=True)
            .first())


def face_uid_of_user(user):
    """شناسهٔ کاربر (با ساختن پروفایل در صورت نبود)."""
    if user is None:
        return None
    if getattr(user, 'pk', None) is None:
        return None
    return ensure_face_uid(user)


def user_by_face_uid(uid):
    """کاربرِ صاحب این شناسه، یا None."""
    if not uid:
        return None
    return (UserProfile.objects
            .select_related('user')
            .filter(face_uid=uid)
            .first()) and UserProfile.objects.select_related('user').get(face_uid=uid).user


def username_by_face_uid(uid):
    """نام کاربریِ صاحب این شناسه، یا None."""
    if not uid:
        return None
    row = (UserProfile.objects
           .filter(face_uid=uid)
           .values_list('user__username', flat=True)
           .first())
    return row


def face_uid_for_label(label):
    """هر برچسبی (شناسه یا نام کاربریِ قدیمی) را به شناسه تبدیل کن.

    گالری‌های قدیمی برچسب‌هایشان نام کاربری است. این تابع اجازه می‌دهد
    مدل قدیمی تا زمانی که مهاجرت اجرا نشده هم درست کار کند.
    """
    if not label:
        return None
    if is_face_uid(label):
        return label
    return face_uid_of(label)


def username_for_label(label):
    """هر برچسبی را به نام کاربریِ فعلی تبدیل کن.

    این تابع پل بین «مدل» (که با شناسه کار می‌کند) و بقیهٔ برنامه
    (که با نام کاربری کار می‌کند) است. اگر برچسب به هیچ کاربری نخورد،
    خودِ برچسب برگردانده می‌شود تا چیزی بی‌سروصدا گم نشود.
    """
    if not label:
        return None
    if is_face_uid(label):
        return username_by_face_uid(label) or label
    return label


def resolve_users_by_labels(labels):
    """چند برچسب را یک‌جا به کاربرهایشان نگاشت می‌کند (با cache کوچک)."""
    result = {}
    uids = [l for l in labels if is_face_uid(l)]
    legacy = [l for l in labels if l and not is_face_uid(l)]
    if uids:
        for uid, username in (UserProfile.objects
                              .filter(face_uid__in=uids)
                              .values_list('face_uid', 'user__username')):
            result[uid] = username
    if legacy:
        for username, uid in (UserProfile.objects
                              .filter(user__username__in=legacy)
                              .values_list('user__username', 'face_uid')):
            if uid:
                result[username] = uid
    return result


# ============================================================
# مسیر پوشهٔ عکس‌ها
# ============================================================
def training_root():
    return TRAINING_DIR


def dataset_dir_for_user(user):
    """پوشهٔ عکس‌های این کاربر (بر اساس شناسه، نه نام کاربری)."""
    uid = face_uid_of_user(user)
    if not uid:
        return None
    return os.path.join(TRAINING_DIR, uid)


def dataset_dir_for_username(username):
    """پوشهٔ عکس‌های این کاربر، با نام کاربری (لایهٔ راحتی)."""
    if not username:
        return None
    from django.contrib.auth.models import User as _User
    user = _User.objects.filter(username=username).first()
    return dataset_dir_for_user(user)


def make_dataset_dir(user):
    """پوشهٔ عکس‌های کاربر را می‌سازد و مسیرش را برمی‌گرداند."""
    path = dataset_dir_for_user(user)
    if not path:
        return None
    os.makedirs(path, exist_ok=True)
    return path


def has_photos(user):
    """آیا این کاربر عکسِ ثبت‌شده دارد؟"""
    path = dataset_dir_for_user(user)
    if not path or not os.path.isdir(path):
        return False
    try:
        return any(f.lower().endswith(('.jpg', '.jpeg', '.png'))
                   for f in os.listdir(path))
    except OSError:
        return False


# ============================================================
# نگاشت پوشه‌های قدیمی (برای اجرای مهاجرت)
# ============================================================
def _close_conn():
    """در تردهای پس‌زمینه اتصال دیتابیس باید دستی بسته شود."""
    try:
        close_old_connections()
    except Exception:
        pass


def ensure_all_face_uids(users=None):
    """برای همهٔ کاربران شناسه می‌سازد. تعداد ساخته‌شده را برمی‌گرداند."""
    _close_conn()
    qs = User.objects.all() if users is None else users
    created = 0
    for user in qs:
        profile = ensure_profile(user)
        if not profile.face_uid:
            ensure_face_uid(user)
            created += 1
    return created