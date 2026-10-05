import threading
import time
import datetime
import os
import cv2
import imutils
import numpy as np
from django.contrib.auth.models import User
from django.utils import timezone
from users.models import (
    Present, Time, Break, ExitReason, AttendanceSnapshot, UserProfile,
)
from users.faceid import (
    face_uid_for_label,
    is_face_uid,
    resolve_users_by_labels,
    dataset_dir_for_username as _face_dataset_dir,
)


HEARTBEAT_TIMEOUT_SEC = 6
DATASET_HEARTBEAT_TIMEOUT_SEC = 20
FRAME_STALL_TIMEOUT_SEC = 10
MIN_FACE_SIZE = 80

# ============================================================================
# صف انتظار دوربین (Camera queue)
# ----------------------------------------------------------------------------
# دوربین یک منبعِ یکتای درون‌فرایندی است، پس در هر لحظه فقط یک نفر می‌تواند
# از آن استفاده کند. نفر دوم قبلاً صرفاً «رد می‌شد» (و ضمناً می‌توانست با یک
# heartbeat یا cancel اشتباه، نشست نفر اول را خراب کند). حالا هر درخواست یک
# «تیکت» می‌گیرد:
#
#   queued    : در صف منتظر است، هنوز دوربین را نگرفته
#   active    : دوربین در اختیار اوست (یا ترد دوربین تمام شده و فقط یک نوشتن
#               نهایی مانده — مثل آموزش مدل پس از ثبت دیتاست)
#   finishing : ترد دوربین تمام شده و دوربین آزاد شده، ولی کاربر هنوز باید
#               دلیل خروج را ثبت کند یا نتیجه را ببیند
#
# قاعدهٔ طلایی: **هیچ نشستی بدون اجازهٔ صاحبش تغییر نمی‌کند.** heartbeat، دیدن
# فریم‌ها، لغو و ثبت دلیل خروج همگی با تیکت/کاربر گیرنده بررسی می‌شوند؛ به همین
# دلیل دو کاربر هم‌زمان دیگر نمی‌توانند نشست یکدیگر را از هم بپاشند.
# ============================================================================
QUEUE_STALE_SEC = 25              # تیکت صف که این مدت پول نزده → از صف حذف می‌شود
QUEUE_MAX_WAIT_SEC = 20 * 60      # سقف انتظار در صف (بعدش به کاربر اطلاع می‌دهیم)
FINISHING_GRACE_SEC = 180         # مهلت نوشتن/خواندن نهایی پس از آزاد شدن دوربین

KIND_IN = 'in'
KIND_OUT = 'out'
KIND_BREAK_OUT = 'break_out'
KIND_BREAK_IN = 'break_in'
KIND_DATASET = 'dataset'

KIND_LABELS = {
    KIND_IN: 'ثبت ورود',
    KIND_OUT: 'ثبت خروج',
    KIND_BREAK_OUT: 'شروع استراحت',
    KIND_BREAK_IN: 'پایان استراحت',
    KIND_DATASET: 'ثبت عکس چهره',
}


# ============================================================================
# مدل تشخیص چهره
# ----------------------------------------------------------------------------
# امبدینگ: ArcFace w600k_r50 (InsightFace، 512-بعدی) - یکی از دقیق‌ترین مدل‌های
# استاندارد تشخیص چهره. روی داده‌های همین پروژه جدایی تقریباً کامل می‌دهد:
# تشابه یک نفر با خودش (min) ~0.67 در حالی که بین دو نفر مختلف (max) ~0.12.
# تشخیص‌گر: YuNet (OpenCV DNN) با 5 نقطه‌ی کلیدی برای ترازبندی دقیق.
# fallback (به ترتیب): SFace (OpenCV DNN) سپس dlib/face_recognition.
#
# نکته‌ی مهم: گالری همیشه فقط با یک نوع embedder ساخته و برچسب‌گذاری می‌شود تا
# فضاهای امبدینگ قاطی نشوند (هر عکسی که با embedder اصلی ساخته نشود رد می‌شود).
# ============================================================================

ARCFACE_MODEL_PATH = 'face_recognition_data/w600k_r50.onnx'
SFACE_MODEL_PATH = 'face_recognition_data/face_recognition_sface_2021dec.onnx'

# نقاط مرجع ترازبندی 112x112 (ترتیب: چشم راست، چشم چپ، بینی، گوشه‌ی راست دهان،
# گوشه‌ی چپ دهان) - دقیقاً مطابق ترتیب 5 نقطه‌ی YuNet و موردانتظار ArcFace.
ARCFACE_REF = np.array([
    [38.2946, 51.6963],
    [73.5318, 51.5014],
    [56.0252, 71.7366],
    [41.5493, 92.3655],
    [70.7299, 92.2041],
], dtype=np.float32)

# آستانه‌ی تشابه (امبدینگ‌های ArcFace/SFace L2-نرمال هستند -> تشابه کسینوسی)
FACE_MATCH_MIN_ARCFACE = 0.40
FACE_MATCH_MIN_SFACE = 0.45
# آستانه برای fallback قدیمی dlib (فاصله - تشابه = 1 - فاصله)
FACE_MATCH_MIN_DLIB = 0.58
FACE_CONFIRM_COUNT = 4
DATASET_TIMEOUT_SEC = 120

# کیفیت / تکراری‌بودن ثبت دیتاست
DATASET_MIN_SHARPNESS = 35.0    # کمترین واریانس لاپلاس (تاری بودن عکس)
DATASET_DEDUP_SIM = 0.985       # عکس‌های خیلی شبیه به عکس قبلی ذخیره نمی‌شوند


def list_video_devices():
    """همهٔ گره‌های /dev/video* مرتب‌شده بر اساس شماره (0, 1, 2, ...)."""
    import glob
    def sort_key(p):
        tail = p.rsplit('video', 1)[1]
        return int(tail) if tail.isdigit() else 10 ** 9
    return sorted(glob.glob('/dev/video*'), key=sort_key)


def get_camera_source():
    """منبع دوربین پیکربندی‌شده:
    - CAMERA_DEVICE=/dev/videoN  (مسیر دستگاه)
    - CAMERA_INDEX=N             (شمارهٔ دستگاه)
    - در غیر این صورت 'auto' (تشخیص خودکار از /dev/video*)
    """
    dev = os.environ.get('CAMERA_DEVICE', '').strip()
    if dev:
        return dev
    idx = os.environ.get('CAMERA_INDEX', '').strip()
    if idx:
        try:
            return int(idx)
        except (TypeError, ValueError):
            pass
    return 'auto'


def enumerate_cameras():
    """Return list of camera dicts [{index, name, path, available}, ...].

    برخلاف نسخهٔ قبلی، دستگاه‌هایی که باز نمی‌شوند هم (با برچسب «در دسترس
    نیست») فهرست می‌شوند تا مشکل دسترسی/اتصال برای مدیر واضح باشد.
    """
    cams = []
    for dev in list_video_devices():
        try:
            idx = int(dev.rsplit('video', 1)[1])
        except (ValueError, IndexError):
            continue
        name = dev
        try:
            with open(f'/sys/class/video4linux/{dev.rsplit("/", 1)[1]}/name') as fh:
                nm = fh.read().strip()
                if nm:
                    name = nm
        except Exception:
            pass
        available = False
        try:
            cap = cv2.VideoCapture(idx, cv2.CAP_V4L2)
            available = bool(cap.isOpened())
            cap.release()
        except Exception:
            available = False
        label = '%s (%s)' % (name, dev)
        if not available:
            label += ' — در دسترس نیست'
        cams.append({'index': idx, 'name': label, 'path': dev, 'available': available})
    if not cams:
        cams.append({'index': 0, 'name': 'دوربین پیش‌فرض', 'path': '/dev/video0', 'available': False})
    return cams


def _CAMERA_BUSY_IMAGE():
    """تصویر ثابت خاکستری با نشان قفل — برای کسی که صاحب دوربین نیست.

    عمداً متن فارسی روی تصویر کشیده نمی‌شود چ��ن OpenCV متن فارسی را درست
    رندر نمی‌کند؛ پیام اصلی را لایهٔ HTML روی تصویر می‌گوید.
    """
    import numpy as _np
    img = _np.full((480, 640, 3), 32, dtype=_np.uint8)
    cv2.rectangle(img, (0, 0), (639, 479), (90, 90, 95), 2)
    # بدنهٔ قفل
    cv2.rectangle(img, (280, 235), (360, 305), (170, 170, 175), -1)
    # قوس بالای قفل
    cv2.ellipse(img, (320, 235), (28, 30), 0, 180, 360, (170, 170, 175), 6)
    cv2.circle(img, (320, 265), 8, (60, 60, 65), -1)
    cv2.rectangle(img, (317, 265), (323, 288), (60, 60, 65), -1)
    return img


def camera_failure_message():
    """پیام خطای واضح وقتی دوربین باز نمی‌شود، با راهنمای رفع مشکل."""
    devs = list_video_devices()
    src = get_camera_source()
    if devs:
        if src != 'auto':
            return ('دوربین باز نشد؛ منبع پیکربندی‌شده (%s) در دسترس نیست. '
                    'دستگاه‌های موجود: %s. کاربر سرویس باید عضو گروه video باشد '
                    '(sudo usermod -aG video kian).' % (src, '، '.join(devs)))
        return ('دوربین باز نشد؛ دسترسی به %s ممکن نیست. '
                'کاربر سرویس باید عضو گروه video باشد '
                '(sudo usermod -aG video kian).' % '، '.join(devs))
    if src != 'auto':
        return ('دوربین باز نشد؛ منبع پیکربندی‌شده (%s) پیدا نشد. '
                'وبکم باید به‌صورت USB passthrough داخل این VM وصل شود.'
                % (src,))
    return ('دوربین سرور پیدا نشد؛ هیچ دستگاه /dev/video* در این VM وجود ندارد. '
            'وبکم باید به‌صورت USB passthrough به این ماشین مجازی وصل شود '
            '(راهنما: فایل CAMERA_SETUP.md).')


# ============================================================================
# تشخیص‌گر (Detector): YuNet با فالبک dlib HOG
# ============================================================================
_DNN_DETECTOR = None
_DLIB_HOG = None


def _get_dnn_detector():
    """Lazy single-instance YuNet detector (OpenCV DNN, state-of-the-art).

    Handles tilted/partially-occluded faces and returns 5 facial landmarks
    which SFace uses for accurate alignment.
    """
    global _DNN_DETECTOR
    if _DNN_DETECTOR is None:
        try:
            _DNN_DETECTOR = cv2.FaceDetectorYN.create(
                'face_recognition_data/face_detection_yunet.onnx', '',
                (320, 320), 0.6, 0.3, 5000)
            print("[INFO] دیتکتور YuNet (OpenCV DNN) آماده شد")
        except Exception as e:
            print(f"[WARN] YuNet در دسترس نیست؛ از dlib HOG استفاده می‌شود: {e}")
            _DNN_DETECTOR = False
    return _DNN_DETECTOR or None


def _get_hog_detector():
    global _DLIB_HOG
    if _DLIB_HOG is None:
        import dlib
        _DLIB_HOG = dlib.get_frontal_face_detector()
    return _DLIB_HOG


def detect_faces_bgr(frame):
    """Detect faces.

    Returns a list of face tuples `(x, y, w, h, landmarks_or_None)`:
      - YuNet yields (x, y, w, h, 5-point landmarks array) - preferred.
      - dlib HOG fallback yields (x, y, w, h, None).
    """
    det = _get_dnn_detector()
    faces = []
    if det is not None:
        h, w = frame.shape[:2]
        det.setInputSize((w, h))
        try:
            ok, results = det.detect(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        except Exception:
            ok, results = 0, None
        if ok and results is not None and len(results) > 0:
            for f in results:
                x, y = int(f[0]), int(f[1])
                fw, fh = int(f[2]), int(f[3])
                lmarks = f[4:14].reshape(5, 2).copy()
                faces.append((x, y, fw, fh, lmarks))
            return faces
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    for r in _get_hog_detector()(gray, 0):
        faces.append((r.left(), r.top(), r.width(), r.height(), None))
    return faces


# ============================================================================
# امبدینگ (Embedder): ArcFace → SFace → dlib (به ترتیب اولویت)
# ============================================================================
_EMBEDDER = None          # 'arcface' | 'sface' | 'dlib' (نوع embedder فعال)
_ARC_NET = None
_SFACE_RECOGNIZER = None


def get_embedder_name():
    """نام مدل امبدینگ فعال: 'arcface' یا 'sface' یا 'dlib'."""
    if _EMBEDDER is None:
        _get_arcface()
    return _EMBEDDER or 'dlib'


def _get_arcface():
    """Lazy single-instance ArcFace embedder (OpenCV DNN). None if unavailable."""
    global _ARC_NET, _EMBEDDER
    if _ARC_NET is None:
        try:
            _ARC_NET = cv2.dnn.readNetFromONNX(ARCFACE_MODEL_PATH)
            _ARC_NET.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
            _ARC_NET.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
            _EMBEDDER = 'arcface'
            print("[INFO] امبدینگ ArcFace (OpenCV DNN) آماده شد")
        except Exception as e:
            print(f"[WARN] ArcFace در دسترس نیست؛ از SFace استفاده می‌شود: {e}")
            _ARC_NET = False
    if _EMBEDDER is None:
        _get_sface()
    return _ARC_NET or None


def _get_sface():
    """Lazy single-instance SFace recognizer. None if unavailable."""
    global _SFACE_RECOGNIZER, _EMBEDDER
    if _SFACE_RECOGNIZER is None:
        try:
            fr = cv2.FaceRecognizerSF.create(
                SFACE_MODEL_PATH, '', cv2.FaceRecognizerSF_FR_COSINE)
            _SFACE_RECOGNIZER = fr
            if _EMBEDDER is None:
                _EMBEDDER = 'sface'
            print("[INFO] امبدینگ SFace (OpenCV DNN) آماده شد")
        except Exception as e:
            print(f"[WARN] SFace در دسترس نیست؛ از dlib/face_recognition "
                  f"به‌عنوان جایگزین استفاده می‌شود: {e}")
            _SFACE_RECOGNIZER = False
            if _EMBEDDER is None:
                _EMBEDDER = 'dlib'
    return _SFACE_RECOGNIZER or None


def _load_primary_embedder():
    """Load the best available embedder (ArcFace → SFace → dlib)."""
    if _EMBEDDER is None:
        _get_arcface()          # arcface یا در صورت نبود، sface/dlib
        if _EMBEDDER is None:
            _get_sface()        # همیشه watchdog نرم افزار
    return _EMBEDDER or 'dlib'


def _l2_normalize(v):
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else None


def embed_aligned_arcface(frame_bgr, x, y, w, h, landmarks):
    """ArcFace 512-d embedding of one detected face (needs 5 landmarks)."""
    net = _get_arcface()
    if net is None or landmarks is None:
        return None
    try:
        M, _ = cv2.estimateAffinePartial2D(
            landmarks.astype(np.float32), ARCFACE_REF, method=cv2.RANSAC)
        if M is None:
            return None
        aligned = cv2.warpAffine(frame_bgr, M, (112, 112), borderValue=(0, 0, 0))
        blob = cv2.dnn.blobFromImage(aligned, 1 / 128.0, (112, 112),
                                     (127.5, 127.5, 127.5), swapRB=True)
        net.setInput(blob)
        feat = net.forward()
        if feat is None or feat.shape[0] == 0:
            return None
        return _l2_normalize(feat[0].astype(np.float32))
    except Exception:
        return None


def embed_aligned_sface(frame_bgr, x, y, w, h, landmarks):
    """SFace embedding of one detected face (needs the 5 landmarks)."""
    fr = _get_sface()
    if fr is None or landmarks is None:
        return None
    try:
        box = np.array([x, y, w, h,
                        landmarks[0][0], landmarks[0][1],
                        landmarks[1][0], landmarks[1][1],
                        landmarks[2][0], landmarks[2][1],
                        landmarks[3][0], landmarks[3][1],
                        landmarks[4][0], landmarks[4][1]],
                       dtype=np.float64)
        aligned = fr.alignCrop(frame_bgr, box)
        if aligned is None or aligned.size == 0:
            return None
        feat = fr.feature(aligned)
        if feat is None or feat.shape[0] == 0:
            return None
        return _l2_normalize(feat[0])
    except Exception:
        return None


def embed_crop_bgr(crop_bgr, num_jitters=1, min_size=96):
    """Fallback: dlib/face_recognition embedding of a face crop (legacy path).

    Returns the raw (not normalized) dlib 128-d vector, matching the classic
    L2-distance matching used for the dlib fallback in `_classify`.
    """
    try:
        import face_recognition
    except Exception:
        return None
    h, w = crop_bgr.shape[:2]
    if h == 0 or w == 0:
        return None
    if min(h, w) < min_size:
        scale = min_size / min(h, w)
        crop_bgr = cv2.resize(
            crop_bgr, (int(w * scale), int(h * scale)),
            interpolation=cv2.INTER_CUBIC)
    rgb = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB)
    encs = face_recognition.face_encodings(
        rgb,
        known_face_locations=[(0, rgb.shape[0] - 1, 0, rgb.shape[1] - 1)],
        num_jitters=num_jitters,
    )
    return encs[0] if encs else None


def _embed_primary(frame_bgr, x, y, w, h, landmarks):
    """Embed with ONLY the active (primary) embedder. Returns None on failure.

    Used for training so every gallery row lives in the same embedding space.
    """
    name = get_embedder_name()
    if name == 'arcface':
        return embed_aligned_arcface(frame_bgr, x, y, w, h, landmarks)
    if name == 'sface':
        return embed_aligned_sface(frame_bgr, x, y, w, h, landmarks)
    hw, hh = frame_bgr.shape[:2]
    m_x = int(w * 0.25)
    m_y = int(h * 0.25)
    x1, y1 = max(x - m_x, 0), max(y - m_y, 0)
    x2, y2 = min(x + w + m_x, hw), min(y + h + m_y, hh)
    if x2 <= x1 or y2 <= y1:
        return None
    return embed_crop_bgr(frame_bgr[y1:y2, x1:x2])


def embed_face_bgr(frame_bgr, x, y, w, h, landmarks):
    """Embed one detected face using the active embedder chain (live path).

    Primary embedder first; falls back to the next only if the face fails it.
    A stray fallback embedding is harmless here because `_classify` applies
    the threshold and would reject a mismatched space as 'unknown'.
    """
    enc = embed_aligned_arcface(frame_bgr, x, y, w, h, landmarks)
    if enc is not None:
        return enc
    enc = embed_aligned_sface(frame_bgr, x, y, w, h, landmarks)
    if enc is not None:
        return enc
    hw, hh = frame_bgr.shape[:2]
    m_x = int(w * 0.25)
    m_y = int(h * 0.25)
    x1, y1 = max(x - m_x, 0), max(y - m_y, 0)
    x2, y2 = min(x + w + m_x, hw), min(y + h + m_y, hh)
    if x2 <= x1 or y2 <= y1:
        return None
    return embed_crop_bgr(frame_bgr[y1:y2, x1:x2])


def compute_encoding(bgr_image, min_size=160):
    """Compute an L2-normalized embedding from a BGR image (training path).

    Detects the largest face (YuNet with HOG fallback), aligns via the
    detected landmarks and embeds it with the ACTIVE primary embedder only
    (keeps the gallery in one embedding space). Returns a one-element list
    or [] when no usable face is found.
    """
    h, w = bgr_image.shape[:2]
    if h == 0 or w == 0:
        return []
    if min(h, w) < min_size:
        scale = min_size / min(h, w)
        bgr_image = cv2.resize(bgr_image, (int(w * scale), int(h * scale)),
                               interpolation=cv2.INTER_LINEAR)
    faces = detect_faces_bgr(bgr_image)
    if not faces:
        return []
    best = max(faces, key=lambda f: f[2] * f[3])
    enc = _embed_primary(bgr_image, best[0], best[1], best[2], best[3], best[4])
    return [enc] if enc is not None else []


GALLERY_FILES = {
    'embeddings':       'face_recognition_data/embeddings.npy',
    'emb_names':        'face_recognition_data/emb_names.npy',
    'emb_prototypes':   'face_recognition_data/emb_prototypes.npy',
    'emb_proto_names':  'face_recognition_data/emb_prototype_names.npy',
    'emb_model_version':'face_recognition_data/emb_model_version.txt',
}


def gallery_is_current():
    """True if the saved gallery belongs to the active embedder."""
    try:
        with open(GALLERY_FILES['emb_model_version'], 'r') as fh:
            return fh.read().strip() == get_embedder_name()
    except Exception:
        return False


def _image_files_in_folder(folder):
    """List jpg/jpeg/png files in a folder (replaces face_recognition's helper)."""
    import re
    return [os.path.join(folder, f) for f in os.listdir(folder)
            if re.match(r'.*\.(jpg|jpeg|png)', f, flags=re.I)]


# ============================================================================
# برچسبِ مدل  ⇄  نام کاربری
# ----------------------------------------------------------------------------
# مدل با «شناسهٔ داخلیِ دائمی» (face_uid) کار می‌کند، ولی بقیهٔ برنامه با
# «نام کاربری». این دو لایه با توابع زیر به هم وصل می‌شوند تا تغییر نام
# کاربری هیچ اثری روی مدل نداشته باشد.
# ============================================================================
def _build_label_map(names):
    """نگاشت «برچسبِ مدل» به «نام کاربریِ فعلی».

    * برچسبِ جدید = شناسهٔ داخلی  → نام کاربری از دیتابیس خوانده می‌شود
    * برچسبِ گالری قدیمی = نام کاربری → همان‌طور که هست

    برچسبی که به هیچ کاربری نمی‌رسد (کاربر حذف شده) مقدار ``None`` می‌گیرد
    تا دیگر به‌عنوان چهرهٔ شناخته‌شده پذیرفته نشود.
    """
    mapping = {}
    if not names:
        return mapping
    resolved = resolve_users_by_labels(names)
    for label in names:
        if not label:
            continue
        if is_face_uid(label):
            mapping[label] = resolved.get(label)
        else:
            mapping[label] = resolved.get(label) or label
    return mapping


def dataset_dir_for_username(username):
    """پوشهٔ عکس‌های یک کاربر — بر اساس شناسهٔ دائمی، نه نام کاربری.

    اگر کاربر به هر دلیلی پروفایل نداشته باشد، به نام کاربری برمی‌گردیم تا
    جریان ثبت عکس متوقف نشود.
    """
    if not username:
        return None
    path = _face_dataset_dir(username)
    if path:
        return path.replace(os.sep, '/') + '/'
    return 'face_recognition_data/training_dataset/%s/' % username


def known_face_labels():
    """همهٔ برچسب‌هایی که مدل باید بشناسد (شناسه‌ها + نام‌های قدیمی).

    برای پاکسازی گالری و تازگی‌سنجی مدل استفاده می‌شود.
    """
    labels = set(UserProfile.objects
                 .exclude(face_uid='')
                 .values_list('face_uid', flat=True))
    labels.update(User.objects.values_list('username', flat=True))
    labels.discard('')
    return labels


def train_gallery():
    """Build the face gallery from the training dataset.

    Walks face_recognition_data/training_dataset/<person>/* and computes a
    normalized embedding per usable photo with the ACTIVE primary embedder
    (ArcFace by default, SFace/dlib fallback). Photos that fail the primary
    embedder are skipped so the whole gallery lives in one embedding space.
    Saves raw per-photo embeddings, per-person median prototypes and a
    version marker so stale galleries from old models are never loaded.

    Returns (photo_count, message).
    """
    training_dir = 'face_recognition_data/training_dataset'
    X = []
    y = []
    per_person = {}
    # پوشه‌هایی که به هیچ کاربری نمی‌خورند (مثلاً کارمندی که حذف شده ولی
    # پوشه‌اش مانده) از مدل بیرون می‌مانند — ولی فایل‌هایشان حذف نمی‌شود.
    orphans = []

    for folder in sorted(os.listdir(training_dir)):
        curr_directory = os.path.join(training_dir, folder)
        if not os.path.isdir(curr_directory):
            continue
        person_name = face_uid_for_label(folder)
        if not person_name:
            orphans.append(folder)
            continue
        person_encs = []
        for imagefile in _image_files_in_folder(curr_directory):
            image = cv2.imread(imagefile)
            if image is None:
                continue
            try:
                encodings = compute_encoding(image)
                if len(encodings) > 0:
                    enc = encodings[0].tolist()
                    X.append(enc)
                    y.append(person_name)
                    person_encs.append(enc)
            except Exception:
                try:
                    os.remove(imagefile)
                except Exception:
                    pass
        if person_encs:
            per_person[person_name] = np.median(np.array(person_encs), axis=0)

    if len(X) == 0:
        return 0, 'هیچ داده‌ای برای آموزش یافت نشد.'

    encodings = np.array(X)
    names = np.array(y)
    np.save(GALLERY_FILES['embeddings'], encodings)
    np.save(GALLERY_FILES['emb_names'], names)

    proto_names = np.array(sorted(per_person.keys()))
    proto_encs = np.array([per_person[n] for n in proto_names])
    np.save(GALLERY_FILES['emb_prototypes'], proto_encs)
    np.save(GALLERY_FILES['emb_proto_names'], proto_names)

    with open(GALLERY_FILES['emb_model_version'], 'w') as fh:
        fh.write(get_embedder_name())

    for stale in ('svc.sav', 'single.sav', 'classes.npy'):
        try:
            os.remove('face_recognition_data/' + stale)
        except Exception:
            pass

    embedder = get_embedder_name().upper()
    msg = (f'آموزش کامل شد ({embedder}: {len(X)} عکس، '
           f'{len(per_person)} نفر)')
    if orphans:
        msg += (' — این پوشه‌ها به هیچ کاربری وصل نیستند و در مدل نیامدند: '
                + '، '.join(orphans))
    return len(X), msg


def prune_gallery_to_existing_users():
    """افرادِ حذف‌شده را از گالری چهره بیرون می‌کشد.

    وقتی ادمین یک کارمند را حذف می‌کند، پوشه‌ی دیتاست او هم پاک می‌شود ولی
    گالری ذخیره‌شده (embeddings / prototypes) همچنان نام او را نگه می‌دارد.
    اگر سیستم با آن نامِ کهنه مواجه شود، ورود/خروج با خطا می‌شکند؛ این تابع
    بعد از حذفِ کاربر صدا زده می‌شود تا مدلِ زنده هم‌سو با کاربران واقعی
    بماند (نیازی به Train کامل نیست).

    Returns (removed_raw_photos, removed_prototypes).
    """
    from django.contrib.auth.models import User
    # هم شناسه‌های داخلی و هم نام‌های کاربری (برای گالری‌های قدیمی)
    existing = known_face_labels()
    removed_raw = 0
    removed_proto = 0
    changed = False

    # ۱) عکس‌های خام هر نفر
    try:
        raw_names = np.load(GALLERY_FILES['emb_names'], allow_pickle=True).tolist()
        raw_embs = np.load(GALLERY_FILES['embeddings'])
        raw_mask = np.array([n in existing for n in raw_names])
        if not raw_mask.all():
            np.save(GALLERY_FILES['embeddings'], raw_embs[raw_mask])
            np.save(GALLERY_FILES['emb_names'],
                    np.array([n for n, keep in zip(raw_names, raw_mask) if keep]))
            removed_raw = int((~raw_mask).sum())
            changed = True
    except FileNotFoundError:
        pass

    # ۲) پروتوتایپ (یک بردار در هر نفر) — همان چیزی که طبقه‌بندی زنده می‌خواند
    try:
        proto_names = np.load(GALLERY_FILES['emb_proto_names'],
                              allow_pickle=True).tolist()
        proto_embs = np.load(GALLERY_FILES['emb_prototypes'])
        proto_mask = np.array([n in existing for n in proto_names])
        if not proto_mask.all():
            np.save(GALLERY_FILES['emb_prototypes'], proto_embs[proto_mask])
            np.save(GALLERY_FILES['emb_proto_names'],
                    np.array([n for n, keep in zip(proto_names, proto_mask) if keep]))
            removed_proto = int((~proto_mask).sum())
            changed = True
    except FileNotFoundError:
        pass

    if changed:
        camera_manager.load_model()
        print(f"[INFO] گالری پاکسازی شد: {removed_raw} عکس، "
              f"{removed_proto} نفر از گالری زنده حذف شد")
    return removed_raw, removed_proto


def update_attendance_in_db_in(present, frame=None):
    """ثبت ورود + عکس لحظهٔ تأیید شده (در صورت در دسترس بودن فریم)."""
    from users.snapshots import save_snapshot
    today = datetime.date.today()
    now = timezone.now()
    for person in present:
        try:
            user = User.objects.get(username=person)
        except User.DoesNotExist:
            # نامی که گالری برگردانده دیگر در سیستم نیست (کارمند حذف شده)؛
            # به‌جای کرش دوربین، این نفر را نادیده بگیر.
            continue
        qs = Present.objects.filter(user=user, date=today).first()
        if qs is None:
            Present(user=user, date=today, present=True).save()
        elif not qs.present:
            qs.present = True
            qs.save(update_fields=['present'])
        last_time = Time.objects.filter(user=user, date=today).order_by('-time').first()
        if last_time is None or last_time.out:
            Time(user=user, date=today, time=now, out=False).save()
            save_snapshot(person, frame, AttendanceSnapshot.EVENT_IN)


def update_attendance_in_db_out(present, frame=None):
    """ثبت خروج + عکس لحظهٔ تأیید شده (در صورت در دسترس بودن فریم)."""
    from users.snapshots import save_snapshot
    today = datetime.date.today()
    now = timezone.now()
    for person in present:
        try:
            user = User.objects.get(username=person)
        except User.DoesNotExist:
            continue
        if Present.objects.filter(user=user, date=today).count() == 0:
            Present(user=user, date=today, present=True).save()
        last_time = Time.objects.filter(user=user, date=today).order_by('-time').first()
        if last_time is None or not last_time.out:
            Time(user=user, date=today, time=now, out=True).save()
            save_snapshot(person, frame, AttendanceSnapshot.EVENT_OUT)


class CameraManager:
    def __init__(self):
        self.lock = threading.Lock()
        self.thread = None
        self.mode = None
        self.active = False
        self.status = "غیرفعال"
        self.match_count = 0
        self.last_name = None
        self.frame_jpeg = None
        self.done = False
        self.marked_username = None
        self.camera_error = None
        self.last_raw_frame = None
        self.dataset_username = None
        self.dataset_count = 0
        self.dataset_target = 0
        self.dataset_done = False
        self.dataset_auto_train = False
        self.dataset_train_status = ""
        self.dataset_train_done = False
        self.dataset_phase = 'idle'
        self.dataset_camera_index = 0
        self.dataset_capture_start = 0.0
        self._last_capture_enc = None
        self.last_heartbeat = time.time()
        self.waiting_exit_reason = False
        self.waiting_exit_reason_username = None
        self.waiting_exit_reason_since = None

        # توکن جلسه (session token): از هم پاشیدن وضعیت یک جلسهٔ تازه توسط
        # پاکسازیِ جلسهٔ قدیمی یا نگه‌داشتن دوربین توسط thread قبلی را
        # غیرممکن می‌کند (مشکل race condition در تعویض سریع جلسه‌ها).
        self.session_serial = 0
        self.session_token = None

        # ---------- صف انتظار و مالکیت نشست ----------
        # self.sessions: تیکت -> دیکشنری نشست (کاربر، نوع کار، وضعیت، ...)
        # self.queue: تیکت‌هایی که در صف منتظرند، به ترتیب ورود
        # self._device_owner_ticket: تیکتی که همین حالا وبکم را در اختیار دارد
        self.sessions = {}
        self.queue = []
        self._ticket_seq = 0
        self._device_owner_ticket = None
        self._cap = None
        self._placeholder_jpeg = None
        # تیکتی که ترد دوربینش تمام شده ولی هنوز باید دلیل خروج را ثبت کند.
        # لازم است چون وبکم در این فاصله به نفر بعدی صف تحویل داده می‌شود و اگر
        # فقط «مالک وبکم» حق ثبت داشته باشد، کاربر اصلاً نمی‌تواند خروجش را ثبت کند.
        self._pending_reason_ticket = None

        # فقط یک آموزش گالری در هر لحظه (دو آموزش هم‌زمان، گالری را خراب می‌کند)
        self._train_lock = threading.Lock()
        self._training = False
        self._refreshing = False

        print("[INFO] بارگذاری مدل تشخیص چهره...")
        self.gallery_encodings = None
        self.gallery_names = None
        self.gallery_per_person = {}
        self._label_usernames = {}
        _get_dnn_detector()
        _load_primary_embedder()
        self.load_model()

    def load_model(self):
        """Load the face gallery produced by the active embedder.

        Stale galleries (e.g. built with the old dlib embedding) are ignored
        and a retrain is required.

        Classification runs against one per-person PROTOTYPE (median of all
        that person's photos) rather than every raw photo. That is more robust
        against noisy single captures and scales to many users.
        """
        self.gallery_encodings = None
        self.gallery_names = None
        self.gallery_per_person = {}
        self._label_usernames = {}
        if not gallery_is_current():
            print("[WARN] گالری ذخیره‌شده متعلق به مدل قبلی است؛ "
                  "دوباره Train کن تا مدل جدید ساخته شود.")
            return
        try:
            proto = np.load(GALLERY_FILES['emb_prototypes'])
            proto_names = np.load(
                GALLERY_FILES['emb_proto_names'], allow_pickle=True).tolist()
            if proto.size > 0 and len(proto_names) == len(proto):
                self.gallery_encodings = proto
                self.gallery_names = proto_names
                self._label_usernames = _build_label_map(proto_names)
                emb = get_embedder_name().upper()
                print(f"[INFO] مدل گالری بارگذاری شد "
                      f"({emb}: {len(proto_names)} نفر)")
            else:
                print("[WARN] گالری خالی است")
        except FileNotFoundError:
            print("[WARN] مدل پیدا نشد -- اول باید Train کنی")

    def is_busy(self):
        with self.lock:
            return self.active

    # ========================================================================
    # صف انتظار و مالکیت نشست
    # ========================================================================
    def _new_ticket_locked(self):
        self._ticket_seq += 1
        return self._ticket_seq

    def _reap_dead_device_locked(self, now):
        """اگر ترد دوربین مرده ولی هنوز فعال است، مالکیت را آزاد کن."""
        if self._device_owner_ticket is None:
            return
        if self.thread is not None and self.thread.is_alive():
            return
        if not self.active:
            return
        ticket = self._device_owner_ticket
        entry = self.sessions.get(ticket)
        if entry is not None:
            entry['state'] = 'finishing'
            entry['finished_at'] = now
            entry['last_seen'] = now
            entry['status_snapshot'] = self._status_snapshot_locked()
            entry['dataset_snapshot'] = self._dataset_snapshot_locked()
        self._device_owner_ticket = None
        self.active = False
        self._cap = None
        print(f"[WARN] ترد دوربين تیکت {ticket} مرده بود؛ مالکيت آزاد شد")

    def _purge_sessions_locked(self, now):
        """حذف تیکت‌های مرده. همیشه باید زیرِ self.lock صدا زده شود.

        تیکت‌های «صف» که دیگر پول نمی‌زنند (صفحه بسته شده، کراش مرورگر، قطع شبکه)
        از صف می‌افتند تا صف برای همیشه قفل نشود. تیکت «active» را دست نمی‌زنیم
        چون ترد دوربین خودش heartbeat را می‌پاید و بعد از HEARTBEAT_TIMEOUT_SEC
        نشست را می‌بندد.
        """
        self._reap_dead_device_locked(now)

        # نشست‌های finishing که مهلت‌شان تمام شده
        for ticket in [t for t, s in self.sessions.items()
                       if s['state'] == 'finishing'
                       and now - s['finished_at'] > FINISHING_GRACE_SEC]:
            self.sessions.pop(ticket, None)
            if ticket in self.queue:
                self.queue.remove(ticket)
            if self._pending_reason_ticket == ticket:
                self._pending_reason_ticket = None
                self.waiting_exit_reason = False
                self.waiting_exit_reason_username = None
                self.waiting_exit_reason_since = None

        # نشست‌های فعالی که دیگر وبکم را ندارند و کاربرشان ناپدید شده
        for ticket in [t for t, s in self.sessions.items()
                       if s['state'] == 'active'
                       and s.get('device_released')
                       and now - s['last_seen'] > FINISHING_GRACE_SEC]:
            self.sessions.pop(ticket, None)

        # تیکت‌های صف که یا خیلی قدیمی‌اند یا دیگر پول نمی‌زنند
        for ticket in list(self.queue):
            s = self.sessions.get(ticket)
            if s is None:
                self.queue.remove(ticket)
                continue
            if (now - s['last_seen'] > QUEUE_STALE_SEC
                    or now - s['enqueued_at'] > QUEUE_MAX_WAIT_SEC):
                self.sessions.pop(ticket, None)
                self.queue.remove(ticket)

    def _queue_position_locked(self, ticket):
        try:
            return self.queue.index(ticket) + 1
        except ValueError:
            return 0

    def _device_is_free_locked(self):
        """آیا وبکم واقعاً آزاد است؟ تا وقتی thread قبلی cap را release نکرده
        باز حساب نمی‌شود؛ وگرنه تلاش برای باز کردن دوبارهٔ همان /dev/videoX
        می‌تواند درایور V4L2 را به هم بریزد."""
        return (self._device_owner_ticket is None
                and not self.active
                and self._cap is None)

    def _device_owner(self):
        with self.lock:
            if self._device_owner_ticket is None:
                return None
            return self.sessions.get(self._device_owner_ticket)

    def is_device_owner(self, username, session_key):
        owner = self._device_owner()
        return bool(owner and owner['username'] == username
                    and owner['session_key'] == session_key)

    def _dispatch_locked(self, entry):
        """شروع ترد دوربین برای یک نشست granted‌شده (باید زیرِ lock صدا زده شود)."""
        kind = entry['kind']
        username = entry['username']
        params = entry.get('params') or {}

        self.active = True
        self.done = False
        self.match_count = 0
        self.last_name = None
        self.marked_username = None
        self.frame_jpeg = None
        self.camera_error = None
        self.status = "در حال روشن کردن دوربین..."
        self.last_heartbeat = time.time()
        self.waiting_exit_reason = False
        self.waiting_exit_reason_username = None
        self.waiting_exit_reason_since = None
        self.last_raw_frame = None
        self.mode = kind
        # «باید با کاربر جاری همخوان باشد» — برای ثبت عکس چهره لازم نیست
        self.expected_username = None if kind == KIND_DATASET else username
        self.break_reason = params.get('reason', '') or ''
        self.break_target_user = username

        if kind == KIND_DATASET:
            # دوربین را اپراتور (کاربر واردشده) نگه می‌دارد، ولی عکس‌ها برای
            # کاربرِ هدف ذخیره می‌شود — پس نامِ گرفته‌شده از params می‌آید.
            self.dataset_username = params.get('target_username') or username
            self.dataset_count = 0
            self.dataset_target = params.get('num_samples', 50)
            self.dataset_auto_train = params.get('auto_train', False)
            self.dataset_train_status = ""
            self.dataset_train_done = False
            self.dataset_done = False
            self.dataset_phase = 'preview'
            self.dataset_camera_index = params.get('camera_index', 0)
            target = self._run_dataset
        elif kind == KIND_BREAK_OUT:
            target = self._run_break_out
        elif kind == KIND_BREAK_IN:
            target = self._run_break_in
        else:
            target = self._run

        token = self._begin_session(kind)
        self.thread = threading.Thread(
            target=self._guarded_runner, args=(target, token, entry['ticket']), daemon=True)
        self.thread.start()

    def _guarded_runner(self, target, token, ticket):
        """پوشش‌دهندهٔ ترد دوربین.

        هیچ استثنای پیش‌بینی‌نشده‌ای نباید دوربین را برای همیشه قفل کند و صف را
        برای همیشه نگه دارد. چون `session_finished` خودش idempotent است، فراخوانی
        دوبارهٔ آن بی‌خطر است.
        """
        try:
            target(token, ticket)
        except BaseException:
            import traceback
            print(f"[ERROR] خطاي پيش‌بينی‌نشده در ترد دوربين (تیکت {ticket}):")
            traceback.print_exc()
            with self.lock:
                if self._device_owner_ticket == ticket:
                    self.camera_error = camera_failure_message()
                    self.status = self.camera_error
        finally:
            self.session_finished(ticket)

    def _maybe_pump_locked(self, now):
        """اگر وبکم آزاد است و کسی در صف ایستاده، نفر بعدی را تحویل بگیر.

        لازم است چون بیشتر نقاط ورود برنامه فقط «می‌خوانند» (مثل صفحهٔ انتظار که
        فقط `queue_snapshot` می‌پرسد). اگر وبکم تازه آزاد شده باشد — مثلاً به
        خاطر reaper یا انقضای heartbeat — ولی اینجا صف جلو نرود، همه منتظر
        می‌مانند تا شاید یک کاربر جدید درخواست بدهد.
        """
        if self.queue and self._device_is_free_locked():
            self._pump_locked(now)

    def _pump_locked(self, now):
        """اگر وبکم آزاد است، نفر بعدی صف را تحویل بگیر.

        باید زیرِ self.lock صدا زده شود. اگر کسی در صف نبود یا نشست‌ها ناپدید
        شده بودند، خودکار به حالت idle برمی‌گردد تا مشتری بفهمد دوربین آزاد است.
        """
        # نکته: اگر reaper دوربینِ گیرکرده را آزاد کند، حلقهٔ زیر بلافاصله
        # نفر بعدی صف را تحویل می‌گیرد؛ پس صف متوقف نمی‌ماند.
        self._purge_sessions_locked(now)
        while self.queue:
            if not self._device_is_free_locked():
                return None
            ticket = self.queue[0]
            self.queue.pop(0)
            entry = self.sessions.get(ticket)
            if entry is None or entry['state'] != 'queued':
                continue
            entry['state'] = 'active'
            entry['granted_at'] = now
            entry['last_seen'] = now
            entry['device_released'] = False
            self._device_owner_ticket = ticket
            try:
                self._dispatch_locked(entry)
            except Exception as exc:
                # نباید ردیف را از صف حذف کنیم که بقیه هم نتوانند استفاده کنند
                self.sessions.pop(ticket, None)
                self._device_owner_ticket = None
                self.active = False
                self._cap = None
                print(f"[ERROR] شروع نشست {entry.get('username')} ممکن نشد: {exc}")
                continue
            return entry
        return None

    def _release_device_locked(self, ticket=None):
        """وبکم را آزاد می‌کند (باید زیرِ lock صدا زده شود).

        فقط نشستی اجازه دارد که خودش وبکم را در اختیار دارد؛ به همین دلیل دو کاربر
        هم‌زمان نمی‌توانند نشست هم را باز/ببندند."""
        if ticket is not None and ticket != self._device_owner_ticket:
            return False
        if self._device_owner_ticket is not None:
            entry = self.sessions.get(self._device_owner_ticket)
            if entry is not None:
                entry['device_released'] = True
                entry['last_seen'] = time.time()
        self._device_owner_ticket = None
        self.active = False
        self._cap = None
        return True

    def session_finished(self, ticket):
        """از داخل ترد دوربین صدا زده می‌شود وقتی کارش تمام شد.

        وبکم آزاد می‌شود و نفر بعدی صف تحویل گرفته می‌شود. اگر نشست هنوز یک نوشتن
        نهایی لازم داشته باشد (ثبت دلیل خروج یا آموزش مدل)، در وضعیت finishing
        می‌ماند تا کاربر بتواند نتیجه را ببیند."""
        now = time.time()
        with self.lock:
            entry = self.sessions.get(ticket)
            if entry is None or entry['state'] != 'active':
                return
            entry['state'] = 'finishing'
            entry['finished_at'] = now
            entry['last_seen'] = now
            entry['device_released'] = True
            # اول وبکم آزاد می‌شود تا snapshop وضعیت، active=False را نشان دهد
            self._release_device_locked(ticket)
            if self.waiting_exit_reason:
                self._pending_reason_ticket = ticket
            entry['status_snapshot'] = self._status_snapshot_locked()
            entry['dataset_snapshot'] = self._dataset_snapshot_locked()
            self._pump_locked(now)

    def leave_queue(self, username, session_key, kind=None):
        """خارج شدن از صف بدون گرفتن وبکم. اگر چیزی برای این کاربر نبود، False."""
        with self.lock:
            self._purge_sessions_locked(time.time())
            ticket = self._find_session_locked(username, session_key, kind)
            if ticket is None:
                return False
            entry = self.sessions.get(ticket)
            if entry and entry['state'] == 'queued':
                if ticket in self.queue:
                    self.queue.remove(ticket)
                self.sessions.pop(ticket, None)
                return True
        return False

    def _find_session_locked(self, username, session_key, kind=None):
        """تیکت نشست «فعال» این کاربر در همین نشست مرورگر (نه finishing)."""
        if not username:
            return None
        best = None
        for ticket, s in self.sessions.items():
            if s['username'] != username or s['session_key'] != session_key:
                continue
            if kind and s['kind'] != kind:
                continue
            if s['state'] == 'finishing':
                continue
            if best is None or s['last_seen'] > self.sessions[best]['last_seen']:
                best = ticket
        return best

    @staticmethod
    def _finishing_awaits_user(entry):
        """آیا نشستِ «در حال اتمام» هنوز کاری از کاربر دارد؟

        اگر کاربر کارش را لغو کرده یا ترد دوربین با خطا/تایم‌اوت تمام شده، نباید
        جلوی تلاش مجددش را بگیرد. ولی برای «ثبت خروج» (که منتظر انتخاب دلیل خروج
        است) و «ثبت عکس چهره» (که منتظر پایان آموزش مدل است) باید همان وضعیت قبلی
        به کاربر نشان داده شود، وگرنه کاربر فکر می‌کند کارش ثبت نشده.
        """
        snap = entry.get('status_snapshot') or {}
        dsnap = entry.get('dataset_snapshot') or {}
        kind = entry.get('kind')

        if kind == KIND_OUT and snap.get('waiting_exit_reason'):
            return True
        if kind == KIND_DATASET:
            if dsnap.get('camera_error'):
                return False
            if dsnap.get('train_done'):
                return False
            return True
        return False

    def _find_finishing_locked(self, username, session_key, kind=None):
        """تازه‌ترین نشستِ «در حال اتمام» این کاربر در همین نشست مرورگر."""
        if not username:
            return None
        best = None
        for ticket, s in self.sessions.items():
            if s['state'] != 'finishing':
                continue
            if s['username'] != username or s['session_key'] != session_key:
                continue
            if kind and s['kind'] != kind:
                continue
            if best is None or s['last_seen'] > self.sessions[best]['last_seen']:
                best = ticket
        return best

    def ticket_of(self, username, session_key, kind=None):
        """تیکت زندهٔ این کاربر (active یا queued یا finishing) یا None.

        برای این است که ویوها بتوانند بفهمند کاربرِ درخواست‌دهنده واقعاً صاحب
        نشست هست یا نه، بدون اینکه به جزئیات داخلی دوربین وابسته شوند.
        """
        now = time.time()
        with self.lock:
            self._purge_sessions_locked(now)
            ticket = self._find_session_locked(username, session_key, kind)
            if ticket is None:
                ticket = self._find_finishing_locked(username, session_key, kind)
            if ticket is not None:
                self.sessions[ticket]['last_seen'] = now
            return ticket

    def state_of(self, ticket):
        """وضعیت یک تیکت: 'queued' / 'active' / 'finishing' / None."""
        if ticket is None:
            return None
        with self.lock:
            entry = self.sessions.get(ticket)
            return entry['state'] if entry else None

    def acquire(self, username, session_key, kind, **params):
        """تلاش برای گرفتن وبکم؛ اگر آزاد نبود وارد صف می‌شود.

        خروجی: (وضعیت، تیکت) که وضعیت یکی از این‌هاست:
          'granted'   : وبکم همین حالا برای این کاربر رزرو شد
          'queued'    : کاربر در صف ایستاده و باید منتظر بماند
          'finishing' : ترد دوربین این کاربر تمام شده ولی هنوز باید دلیل خروج را
                        ثبت کند یا نتیجه را ببیند (مثلاً بعد از یک رفرش صفحه)
          'error'     : دوربین اصلاً باز نشد
        """
        now = time.time()
        with self.lock:
            self._purge_sessions_locked(now)

            # اگر همین کاربر در همین نوع کار، نشست فعالی دارد، تکراری نیست
            existing = self._find_session_locked(username, session_key, kind)
            if existing is not None:
                entry = self.sessions[existing]
                entry['last_seen'] = now
                if self._device_owner_ticket == existing:
                    return 'granted', existing
                if entry['state'] == 'queued':
                    # همان تیکت قبلی در صف؛ جای تازه‌ای رزرو نمی‌کنیم
                    return 'queued', existing

            # نشستِ در حال اتمام: کاربر دوباره صفحه را باز کرده (رفرش).
            # نباید دوربین را دوباره قفل کنیم؛ فقط وضعیت قبلی به او برگردد.
            finishing = self._find_finishing_locked(username, session_key, kind)
            if finishing is not None:
                self.sessions[finishing]['last_seen'] = now
                if self._finishing_awaits_user(self.sessions[finishing]):
                    return 'finishing', finishing
                # کاربر کارش تمام شده؛ راه را باز می‌کنیم تا بتواند دوباره تلاش کند
                self.sessions.pop(finishing, None)

            if self._device_is_free_locked():
                ticket = self._new_ticket_locked()
                self.sessions[ticket] = {
                    'username': username,
                    'session_key': session_key,
                    'kind': kind,
                    'params': params,
                    'state': 'active',
                    'ticket': ticket,
                    'enqueued_at': now,
                    'granted_at': now,
                    'last_seen': now,
                    'device_released': False,
                    'finished_at': None,
                }
                self._device_owner_ticket = ticket
                try:
                    self._dispatch_locked(self.sessions[ticket])
                except Exception as exc:
                    self.sessions.pop(ticket, None)
                    self._device_owner_ticket = None
                    self.active = False
                    self._cap = None
                    print(f"[ERROR] شروع دوربین برای {username} ممکن نشد: {exc}")
                    return 'error', None
                return 'granted', ticket

            # وبکم مشغول است → صف
            ticket = self._new_ticket_locked()
            self.sessions[ticket] = {
                'username': username,
                'session_key': session_key,
                'kind': kind,
                'params': params,
                'state': 'queued',
                'ticket': ticket,
                'enqueued_at': now,
                'granted_at': None,
                'last_seen': now,
                'device_released': False,
                'finished_at': None,
            }
            self.queue.append(ticket)
            return 'queued', ticket

    def queue_snapshot(self, username, session_key):
        """وضعیت صف برای نمایش به کاربر.

        شامل می‌شود: آیا این کاربر صاحب وبکم است؟ اگر نه، نوبت او چند است؟
        چه کسی الان وبکم را دارد؟ (برای اینکه کاربر بداند منتظر کیست)"""
        now = time.time()
        with self.lock:
            self._purge_sessions_locked(now)
            self._maybe_pump_locked(now)
            position = None
            in_queue = False
            is_owner = False
            waiting_for_me = None

            ticket = self._find_session_locked(username, session_key)
            if ticket is not None:
                if self._device_owner_ticket == ticket:
                    is_owner = True
                else:
                    in_queue = True
                    position = self._queue_position_locked(ticket)
                    if ticket == (self.queue[0] if self.queue else None):
                        waiting_for_me = True

            holder = None
            if self._device_owner_ticket is not None:
                holder = self.sessions.get(self._device_owner_ticket)

            ahead = 0
            if position:
                ahead = position - 1

            # اگر این کاربر صاحب وبکم است، همین پول زدن heartbeat اوست تا
            # ترد دوربین در فاصلهٔ بین دو پول زدن از کار نیفتد.
            if is_owner:
                self.last_heartbeat = now

            waiting_list = []
            for t in self.queue:
                e = self.sessions.get(t)
                if e is not None:
                    waiting_list.append({'username': e['username'], 'kind': e['kind'],
                                         'since': e['enqueued_at']})
            # نام‌ها را به ترتیب اولویت نمایش می‌دهیم
            waiting_list.sort(key=lambda x: x['since'])

            is_finishing = False
            if is_owner:
                state = 'active'
            elif in_queue:
                state = 'queued'
            elif self._find_finishing_locked(username, session_key) is not None:
                state = 'finishing'
                is_finishing = True
            else:
                state = 'idle'

            return {
                'state': state,
                'camera_available': self._device_is_free_locked(),
                'is_owner': is_owner,
                'is_finishing': is_finishing,
                'in_queue': in_queue,
                'position': position,
                'ahead': ahead,
                'queue_length': len(self.queue),
                'your_turn': bool(is_owner or waiting_for_me),
                'busy_user': holder['username'] if holder else None,
                'busy_kind': holder['kind'] if holder else None,
                'busy_kind_label': KIND_LABELS.get(holder['kind']) if holder else None,
                'busy_since': holder['granted_at'] if holder else None,
                'waiting': waiting_list,
                'max_wait_sec': QUEUE_MAX_WAIT_SEC,
                'stale_after_sec': QUEUE_STALE_SEC,
            }

    def owner_status(self):
        """وضعیت لحظه‌ای وبکم برای کسی که واقعاً صاحب آن است (و گزارش صفر)."""
        with self.lock:
            if self._device_owner_ticket is None:
                return {'active': False, 'status': 'دوربین آزاد است'}
            entry = self.sessions.get(self._device_owner_ticket)
            if entry is None:
                return {'active': False, 'status': 'دوربین آزاد است'}
            return {
                'active': True,
                'status': 'در حال استفاده توسط «%s»' % entry['username'],
                'owner': entry['username'],
            }

    def touch_heartbeat(self, ticket):
        """فقط صاحب وبکم می‌تواند heartbeat بزند؛ وگرنه یک کاربر دوم که صرفاً صفحهٔ
        وضعیت را باز دارد، عمر نشستِ نفر اول را بی‌نهایت تمدید می‌کرد و دوربین
        برای همیشه اشغال می‌ماند."""
        with self.lock:
            if self._device_owner_ticket == ticket:
                self.last_heartbeat = time.time()

    def force_reset(self):
        """آزادسازی اضطراری قفل دوربین (استفادهٔ دستی/مدیریتی)."""
        with self.lock:
            self.active = False
            self.status = "به‌صورت دستی بازنشانی شد"
            self.frame_jpeg = None
            self.waiting_exit_reason = False
            self.waiting_exit_reason_username = None
            self.waiting_exit_reason_since = None
            self.done = False
            self.marked_username = None
            self._cap = None  # Signal camera thread to release
            if self._device_owner_ticket is not None:
                entry = self.sessions.get(self._device_owner_ticket)
                if entry is not None:
                    entry['state'] = 'finishing'
                    entry['finished_at'] = time.time()
            self._device_owner_ticket = None
            self._pending_reason_ticket = None
            self._pump_locked(time.time())

    def cancel_attendance(self, username=None, session_key=None):
        """لغو نشستِ فعالِ متعلق به همین کاربر.

        سه قاعدهٔ مهم که جلوی خراب‌شدن سرور را می‌گیرد:

        ۱) اگر کاربر فقط در صف بوده، **هیچ اتفاقی نمی‌افتد**. خروج از صف کارِ
           جداگانه (`leave_queue`) است. اگر این دو را یکی کنیم، یک beacon
           کهنهٔ «انصراف» (که از `beforeunload` صفحهٔ استریم می‌آید) می‌تواند
           کاربری را که منتظر نوبت است بی‌صدا از صف بیرون بیندازد.
        ۲) اگر کاربر صاحب وبکم باشد، وبکم آزاد و به نفر بعدی صف تحویل داده می‌شود.
        ۳) اگر نشستی متعلق به این کاربر نباشد، هیچ اتفاقی نمی‌افتد — یعنی یک
           کاربر نمی‌تواند نشست کاربر دیگر را از راه دور لغو کند.
        """
        with self.lock:
            self._purge_sessions_locked(time.time())
            ticket = self._find_session_locked(username, session_key)
            if ticket is None:
                return True

            entry = self.sessions.get(ticket)
            if entry is None or entry['state'] == 'queued':
                return True  # در صف است → دست‌نخورده

            if ticket == self._device_owner_ticket:
                entry['state'] = 'finishing'
                entry['finished_at'] = time.time()
                entry['last_seen'] = entry['finished_at']
                entry['status_snapshot'] = dict(self._status_snapshot_locked())
                self.status = "لغو شده توسط کاربر"
                if self._pending_reason_ticket == ticket:
                    self._pending_reason_ticket = None
                self._release_device_locked(ticket)
                self._pump_locked(time.time())
            return True


    def _release_camera(self, cap):
        """Safely release camera capture."""
        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass

    def _begin_session(self, kind):
        """شمارهٔ سریال جلسه را بالا می‌برد و توکن این جلسه را برمی‌گرداند.

        باید داخل lock صدا زده شود. هر thread (جلسه) توکن خودش را می‌گیرد و
        فقط وقتی توکنش هنوز برابر session_token است اجازه دارد وضعیت مشترک
        (active/status/...) را تغییر دهد — به این ترتیب پاکسازی یک جلسهٔ
        قدیمی نمی‌تواند یک جلسهٔ تازه شروع‌شده را خراب کند.
        """
        self.session_serial += 1
        self.session_token = (self.session_serial, kind)
        return self.session_token



    def _open_camera(self, index=None):
        """باز کردن دوربین با پشتیبانی از:
        - CAMERA_DEVICE=/dev/videoN (مسیر دستگاه)
        - CAMERA_INDEX=N (شمارهٔ دستگاه)
        - fallback خودکار: V4L2 → بک‌اند پیش‌فرض OpenCV → مسیر /dev/videoN
        - تلاش مجدد کوتاه (اگر جلسهٔ قبلی هنوز دوربین را رها نکرده باشد)
        """
        src = get_camera_source()
        if isinstance(src, int):
            index = src

        if isinstance(index, str):
            candidates = [index]
        else:
            index = int(index) if index is not None else 0
            candidates = [index, '/dev/video%d' % index]

        cap = None
        for _attempt in range(3):
            for cand in candidates:
                try:
                    cap = cv2.VideoCapture(cand, cv2.CAP_V4L2)
                    if not cap.isOpened():
                        cap = cv2.VideoCapture(cand)
                except Exception:
                    cap = None
                if cap is not None and cap.isOpened():
                    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                    cap.set(cv2.CAP_PROP_FPS, 30)
                    return cap
                if cap is not None:
                    try:
                        cap.release()
                    except Exception:
                        pass
            time.sleep(0.4)
        return cap

    def _model_is_fresh(self):
        """True if the loaded model covers exactly every user that has photos
        AND the gallery was built with the current embedder."""
        if not gallery_is_current():
            return False
        training_dir = 'face_recognition_data/training_dataset'
        if not os.path.isdir(training_dir):
            return True
        holders = set()
        for name in os.listdir(training_dir):
            d = os.path.join(training_dir, name)
            if not os.path.isdir(d):
                continue
            if any(os.path.splitext(f)[1].lower() in ('.jpg', '.jpeg', '.png')
                   for f in os.listdir(d)):
                # پوشه‌های بی‌صاحب جزو مدل نیستند
                label = face_uid_for_label(name)
                if label:
                    holders.add(label)
        if not holders:
            return True
        if self.gallery_encodings is None or self.gallery_names is None:
            return False
        return set(self.gallery_names) == holders

    def _refresh_model_async(self):
        """Rebuild the model in the background so every user with photos is included."""
        if getattr(self, '_refreshing', False):
            return
        with self.lock:
            if getattr(self, '_training', False):
                return
            self._refreshing = True
            self._training = True

        def do_refresh():
            with self.lock:
                self.status = "در حال به‌روزرانی مدل چهره..."
            try:
                self._auto_train_model()
            finally:
                with self.lock:
                    self._refreshing = False
                    self._training = False

        t = threading.Thread(target=do_refresh, daemon=True)
        t.start()

    def _start_watchdog(self, cap, stop_event, last_frame_time):
        def watchdog():
            while not stop_event.is_set():
                time.sleep(1)
                with self.lock:
                    still_active = self.active
                if not still_active:
                    stop_event.set()
                    try:
                        cap.release()
                    except Exception:
                        pass
                    break
                if time.time() - last_frame_time[0] > FRAME_STALL_TIMEOUT_SEC:
                    with self.lock:
                        self.status = "دوربین پاسخ نمی‌دهد؛ در حال آزادسازی اجباری..."
                    try:
                        cap.release()
                    except Exception:
                        pass
                    stop_event.set()
                    break
        t = threading.Thread(target=watchdog, daemon=True)
        t.start()
        return t

    def _expand_box(self, x, y, w, h, img_w, img_h, margin=0.35):
        mx = int(w * margin)
        my = int(h * margin)
        x1 = max(x - mx, 0)
        y1 = max(y - my, 0)
        x2 = min(x + w + mx, img_w)
        y2 = min(y + h + my, img_h)
        return x1, y1, x2, y2

    def _resolve_label(self, label):
        """برچسبِ مدل (شناسهٔ دائمی) را به نام کاربریِ فعلی تبدیل می‌کند.

        نتیجه کش می‌شود چون این تابع برای هر فریم صدا زده می‌شود. اگر
        برچسب در نگاشت نبود (گالری تازه ساخته شده، یا کاربر حذف شده) نگاشت
        یک‌بار از نو ساخته می‌شود.
        """
        if label in self._label_usernames:
            return self._label_usernames[label]
        self._label_usernames = _build_label_map(self.gallery_names or [])
        return self._label_usernames.get(label)

    def _classify(self, encoding):
        """Classify a live embedding against the whole gallery.

        arcface/sface gallery rows are L2-normalized -> cosine similarity.
        dlib fallback rows are raw -> similarity = 1 - L2 distance.

        خروجی «نام کاربری» است (نه شناسهٔ داخلی) تا بقیهٔ برنامه مثل قبل
        کار کند. اگر برچسبِ مدل به هیچ کاربری نرسد، چهره «ناشناس» می‌ماند.
        Returns (name, confidence) or (None, confidence).
        """
        if self.gallery_encodings is None or len(self.gallery_encodings) == 0:
            return None, 0.0
        emb = get_embedder_name()
        if emb == 'dlib':
            sims = 1.0 - np.linalg.norm(self.gallery_encodings - encoding, axis=1)
            threshold = FACE_MATCH_MIN_DLIB
        elif emb == 'arcface':
            sims = self.gallery_encodings @ encoding
            threshold = FACE_MATCH_MIN_ARCFACE
        else:
            sims = self.gallery_encodings @ encoding
            threshold = FACE_MATCH_MIN_SFACE
        idx = int(np.argmax(sims))
        best_sim = float(sims[idx])
        if best_sim < threshold:
            return None, best_sim
        return self._resolve_label(self.gallery_names[idx]), best_sim

    def _process_frame(self, frame):
        img_h, img_w = frame.shape[:2]

        for face in detect_faces_bgr(frame):
            x, y, w, h, lmarks = face
            if w < MIN_FACE_SIZE or h < MIN_FACE_SIZE:
                continue

            x1, y1 = max(x, 0), max(y, 0)
            x2, y2 = min(x + w, img_w), min(y + h, img_h)

            enc = embed_face_bgr(frame, x, y, w, h, lmarks)
            if enc is None:
                continue

            name, conf = self._classify(enc)
            if name:
                cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
                cv2.putText(frame, f"{name}", (x1, y1 - 10),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
                with self.lock:
                    # Verify matched face matches expected user
                    if self.expected_username and name != self.expected_username:
                        self.status = f"این چهره با کاربر جاری ({self.expected_username}) همخوانی ندارد"
                        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 0, 255), 2)
                        cv2.putText(frame, "مجاز نیست", (x1, y1 - 30),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
                    else:
                        if name == self.last_name:
                            self.match_count += 1
                        else:
                            self.last_name = name
                            self.match_count = 1
                        self.status = f"شناسایی شد: {name} ({self.match_count}/{FACE_CONFIRM_COUNT})"
                        if self.match_count >= FACE_CONFIRM_COUNT and not self.done and not self.waiting_exit_reason:
                            if self.mode == 'in':
                                present = {name: True}
                                update_attendance_in_db_in(present, frame)
                                self.done = True
                                self.marked_username = name
                                self.status = f"ورود {name} با موفقیت ثبت شد"
                            else:
                                self.waiting_exit_reason = True
                                self.waiting_exit_reason_username = name
                                self.waiting_exit_reason_since = time.time()
                                self.marked_username = name
                                self.status = f"خروج {name} — دلیل را انتخاب کنید"
            else:
                cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 0, 255), 2)
                cv2.putText(frame, "ناشناس", (x1, y1 - 10),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)

    def _run(self, my_token, my_ticket):
        print("[INFO] در حال باز کردن وبکم سرور...")
        cap = self._open_camera()
        if not cap.isOpened():
            with self.lock:
                if self.session_token == my_token:
                    self.camera_error = camera_failure_message()
                    self.status = self.camera_error
                    self.active = False
            self.session_finished(my_ticket)
            return

        with self.lock:
            self.status = "لطفاً جلوی دوربین بایستید"
            self._cap = cap  # Store for force_reset

        idle_since_done = None
        last_process = 0.0

        stop_event = threading.Event()
        last_frame_time = [time.time()]
        self._start_watchdog(cap, stop_event, last_frame_time)

        while True:
            if stop_event.is_set():
                break

            with self.lock:
                if not self.active or self._cap is None or self.session_token != my_token:  # Check reset signal
                    stop_event.set()
                    break
                heartbeat_age = time.time() - self.last_heartbeat

            if heartbeat_age > HEARTBEAT_TIMEOUT_SEC:
                with self.lock:
                    self.status = "اتصال با مرورگر قطع شد؛ دوربین آزاد شد"
                stop_event.set()
                break

            ret, frame = cap.read()
            if not ret or frame is None:
                time.sleep(0.05)
                continue

            last_frame_time[0] = time.time()
            fh, fw = frame.shape[:2]
            if fw != 640:
                frame = cv2.resize(frame, (640, 480), interpolation=cv2.INTER_LINEAR)

            with self.lock:
                self.last_raw_frame = frame.copy()

            now = time.time()
            if self.gallery_encodings is not None and now - last_process >= 0.15:
                last_process = now
                try:
                    self._process_frame(frame)
                except Exception:
                    # هر خطای پیش‌بینی‌نشده‌ای نباید ترد دوربین را بکشد و
                    # وضعیت «دوربین در حال استفاده» را برای همیشه قفل کند.
                    import traceback
                    traceback.print_exc()
                    with self.lock:
                        self.status = "خطا در پردازش چهره؛ دوربین ادامه می‌دهد"

            ok, jpeg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
            if ok:
                with self.lock:
                    self.frame_jpeg = jpeg.tobytes()

            with self.lock:
                done_now = self.done
                waiting_reason_now = self.waiting_exit_reason

            if done_now:
                if idle_since_done is None:
                    idle_since_done = time.time()
                elif time.time() - idle_since_done > 3:
                    break
            elif waiting_reason_now:
                break

            time.sleep(0.03)

        stop_event.set()
        self._release_camera(cap)
        with self.lock:
            if self.session_token == my_token:
                self._cap = None
                self.active = False
                self.frame_jpeg = None
        # نفر بعدی صف باید بتواند دوربین را تحویل بگیرد
        self.session_finished(my_ticket)
        print("[INFO] دوربین سرور بسته شد")

    def _placeholder_image(self):
        """تصویر ثابت «دوربین در دسترس نیست» برای کسی که صاحب دوربین نیست."""
        img = _CAMERA_BUSY_IMAGE()
        ok, jpeg = cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY, 70])
        return jpeg.tobytes() if ok else b''

    def get_frame(self, ticket=None):
        """فریم وبکم.

        اگر ticket داده شود و صاحب همان تیکت باشد فریم واقعی برگردانده می‌شود،
        وگرنه فقط تصویر «در حال استفاده» — تا دو نفر هم‌زمان تصویر هم را نبینند
        و کسی که صاحب دوربین نیست، heartbeat کسی دیگر را هم تمدید نکند.
        """
        with self.lock:
            if ticket is not None and ticket == self._device_owner_ticket:
                if self.frame_jpeg:
                    return self.frame_jpeg
            if self._placeholder_jpeg is None:
                self._placeholder_jpeg = self._placeholder_image()
            return self._placeholder_jpeg

    def _status_snapshot_locked(self):
        return {
            'status': self.status,
            'done': self.done,
            'username': self.marked_username,
            'camera_error': self.camera_error,
            'active': self.active,
            'waiting_exit_reason': self.waiting_exit_reason,
            'waiting_exit_reason_username': self.waiting_exit_reason_username,
        }

    def _dataset_snapshot_locked(self):
        return {
            'status': self.status,
            'done': self.dataset_done,
            'count': self.dataset_count,
            'target': self.dataset_target,
            'phase': self.dataset_phase,
            'camera_error': self.camera_error,
            'train_status': self.dataset_train_status,
            'train_done': self.dataset_train_done,
            'auto_train': self.dataset_auto_train,
        }

    def get_status(self, ticket=None):
        """وضعیت وبکم برای نمایش. اگر ticket داده شود heartbeat تمدید می‌شود
        (فقط اگر همان ticket صاحب وبکم باشد)."""
        if ticket is not None:
            self.touch_heartbeat(ticket)
        with self.lock:
            if ticket is not None:
                entry = self.sessions.get(ticket)
                if entry is not None and entry['state'] == 'finishing':
                    # بعد از پایان کار، آخرین وضعیت ثبت‌شده را نشان بده
                    snap = dict(entry.get('status_snapshot') or {})
                    snap['finished'] = True
                    return snap
            return self._status_snapshot_locked()

    def confirm_exit_reason(self, ticket, reason_id, reason_text):
        """ثبت دلیل خروج پس از تأیید چهره.

        فقط صاحب همین تیکت اجازه دارد. بدون این بررسی، اگر کاربر «الف» منتظر
        انتخاب دلیل خروج بود و کاربر «ب» هم‌زمان وارد صفحه‌ی استریم خودش می‌شد،
        فرم ب می‌توانست برای الف رکورد خروج ثبت کند.
        """
        with self.lock:
            # مالکیت را با «تیکتِ منتظرِ دلیل خروج» می‌سنجیم نه با مالک وبکم:
            # وبکم به نفر بعدی صف داده شده، ولی ثبت دلیل خروج هنوز وظیفهٔ همین
            # کاربر است و کسی دیگر حق ثبت آن را ندارد.
            if self._pending_reason_ticket != ticket:
                return False, "این نشست متعلق به شما نیست"
            entry = self.sessions.get(ticket) or {}
            snap = entry.get('status_snapshot') or {}
            # از snapshot خودِ این نشست می‌خوانیم، نه از وضعیت زندهٔ سراسری؛ چون
            # وبکم به نفر بعدی داده شده و او وضعیت سراسری را بازنویسی کرده است.
            waiting = snap.get('waiting_exit_reason', self.waiting_exit_reason)
            username = (snap.get('waiting_exit_reason_username')
                        or self.waiting_exit_reason_username)
            if not waiting:
                return False, "در حال انتظار برای دلیل خروج نیست"
            if not username:
                return False, "نام کاربری یافت نشد"

        try:
            from users.models import Time, ExitReason
            today = datetime.date.today()
            now = timezone.now()
            user = User.objects.get(username=username)
            if Present.objects.filter(user=user, date=today).count() == 0:
                Present(user=user, date=today, present=True).save()
            last_time = Time.objects.filter(user=user, date=today).order_by('-time').first()
            if last_time is None or not last_time.out:
                reason_obj = None
                if reason_id:
                    try:
                        reason_obj = ExitReason.objects.get(id=reason_id)
                    except (ExitReason.DoesNotExist, ValueError, TypeError):
                        # گزینهٔ «سایر موارد» یک pk ندارد و رشتهٔ 'other' می‌فرستد؛
                        # قبلاً ValueError به کل ثبت خروج آسیب می‌زد.
                        reason_obj = None
                Time.objects.create(
                    user=user,
                    date=today,
                    time=now,
                    out=True,
                    exit_reason=reason_obj,
                    exit_reason_text=reason_text or ''
                )

            # عکس لحظهٔ تأیید خروج (فریمی که چهره روی آن شناسایی شد)
            try:
                from users.snapshots import save_snapshot
                with self.lock:
                    out_frame = self.last_raw_frame
                save_snapshot(username, out_frame, AttendanceSnapshot.EVENT_OUT)
            except Exception:
                pass

            with self.lock:
                self.waiting_exit_reason = False
                self.waiting_exit_reason_username = None
                self.waiting_exit_reason_since = None
                self.active = False
                self.frame_jpeg = None
                self._cap = None
                self.done = True
                self.status = f"خروج {username} با موفقیت ثبت شد"
                self._pending_reason_ticket = None
                entry = self.sessions.get(ticket)
                if entry is not None:
                    entry['state'] = 'finishing'
                    entry['finished_at'] = time.time()
                    entry['last_seen'] = entry['finished_at']
                    entry['status_snapshot'] = self._status_snapshot_locked()
                    entry['dataset_snapshot'] = self._dataset_snapshot_locked()
                self._release_device_locked(ticket)
                self._pump_locked(time.time())
            return True, "موفق"
        except Exception as e:
            return False, f"خطا: {e}"

    def confirm_dataset_capture(self, ticket):
        """شروع ذخیرهٔ عکس‌ها (گذار preview -> capturing).

        فقط صاحب همین تیکت اجازه دارد؛ وگرنه یک کاربر می‌توانست با یک کلیک،
        ثبت عکس چهرهٔ کاربر دیگری را شروع کند یا عکس‌های او را پاک کند.
        """
        with self.lock:
            if self._device_owner_ticket != ticket:
                return False
            if not self.active or self.dataset_phase != 'preview':
                return False
            self.dataset_phase = 'capturing'
            self.dataset_count = 0
            self.dataset_done = False
            self.dataset_capture_start = time.time()
            self._last_capture_enc = None
            self.status = "در حال گرفتن عکس..."
            self.last_heartbeat = time.time()

        directory = dataset_dir_for_username(self.dataset_username)
        os.makedirs(directory, exist_ok=True)
        for f in os.listdir(directory):
            if os.path.splitext(f)[1].lower() in ('.jpg', '.jpeg', '.png'):
                try:
                    os.remove(os.path.join(directory, f))
                except Exception:
                    pass
        return True

    def cancel_dataset_capture(self, ticket):
        """لغو ثبت عکس — فقط توسط صاحب همین تیکت."""
        with self.lock:
            if self._device_owner_ticket != ticket:
                return False
            self.dataset_phase = 'idle'
            self.active = False
            self.status = "عملیات لغو شد"
            self.frame_jpeg = None
            self._cap = None
            if ticket in self.sessions:
                self.sessions[ticket]['state'] = 'finishing'
                self.sessions[ticket]['finished_at'] = time.time()
                self.sessions[ticket]['status_snapshot'] = self._status_snapshot_locked()
                self.sessions[ticket]['dataset_snapshot'] = self._dataset_snapshot_locked()
            self._release_device_locked(ticket)
            self._pump_locked(time.time())
            return True

    def _is_usable_capture(self, frame, x, y, w, h):
        """True اگر چهره شارپ باشد (کمترین واریانس لاپلاس)."""
        margin = 0.25
        x1 = max(int(x - w * margin), 0)
        y1 = max(int(y - h * margin), 0)
        x2 = min(int(x + w + w * margin), frame.shape[1])
        y2 = min(int(y + h + h * margin), frame.shape[0])
        if x2 <= x1 or y2 <= y1:
            return False
        gray = cv2.cvtColor(frame[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY)
        lap_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        return lap_var >= DATASET_MIN_SHARPNESS

    def _too_similar_to_last_capture(self, enc):
        """True اگر امبدینگ تقریباً با عکس قبلی یکی باشد (جلوگیری از تکرار)."""
        with self.lock:
            prev = self._last_capture_enc
        if prev is None:
            with self.lock:
                self._last_capture_enc = enc.copy()
            return False
        if get_embedder_name() != 'dlib':
            sim = float(np.dot(enc, prev))
        else:
            sim = float(1.0 - np.linalg.norm(enc - prev))
        self._last_capture_enc = enc.copy()
        return sim > DATASET_DEDUP_SIM

    def _run_dataset(self, my_token, my_ticket):
        username = self.dataset_username
        num_samples = self.dataset_target
        # پوشه بر اساس شناسهٔ دائمی است تا تغییر نام کاربری عکس‌هایش را جابه‌جا نکند
        directory = dataset_dir_for_username(username)
        os.makedirs(directory, exist_ok=True)

        print("[INFO] در حال باز کردن دوربین سرور برای ثبت دیتاست...")
        cap = self._open_camera(self.dataset_camera_index)
        if not cap.isOpened():
            with self.lock:
                if self.session_token == my_token:
                    self.camera_error = camera_failure_message()
                    self.status = self.camera_error
                    self.active = False
                    self.dataset_done = True
                    self.dataset_phase = 'idle'
            self.session_finished(my_ticket)
            return

        with self.lock:
            self.status = "دوربین متصل شد؛ آماده هستید؟"

        sample_num = 0

        stop_event = threading.Event()
        last_frame_time = [time.time()]
        self._start_watchdog(cap, stop_event, last_frame_time)

        while True:
            if stop_event.is_set():
                with self.lock:
                    phase = self.dataset_phase
                if phase == 'capturing':
                    break
                break

            with self.lock:
                if not self.active or self.session_token != my_token:
                    stop_event.set()
                    break
                heartbeat_age = time.time() - self.last_heartbeat
                phase = self.dataset_phase

            if heartbeat_age > DATASET_HEARTBEAT_TIMEOUT_SEC:
                with self.lock:
                    self.status = "اتصال با مرورگر قطع شد؛ دوربین آزاد شد"
                stop_event.set()
                break

            ret, frame = cap.read()
            if not ret or frame is None:
                time.sleep(0.05)
                continue

            last_frame_time[0] = time.time()
            fh, fw = frame.shape[:2]
            if fw != 640:
                frame = cv2.resize(frame, (640, 480), interpolation=cv2.INTER_LINEAR)
            faces = detect_faces_bgr(frame)

            if phase == 'preview':
                for face in faces:
                    x, y, w, h, _ = face
                    if w < MIN_FACE_SIZE or h < MIN_FACE_SIZE:
                        continue
                    cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)
                    cv2.putText(frame, "آماده!", (x, y - 10),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
                    break
                with self.lock:
                    self.status = "آماده هستید؟ دکمه «شروع گرفتن عکس» را بزنید"
            elif phase == 'capturing':
                if time.time() - self.dataset_capture_start > DATASET_TIMEOUT_SEC:
                    stop_event.set()
                    break
                for face in faces:
                    x, y, w, h, lmarks = face
                    if w < MIN_FACE_SIZE or h < MIN_FACE_SIZE:
                        continue
                    # ثبت فقط فریم‌های شارپ و متفاوت برای دیتاست باکیفیت‌تر
                    if not self._is_usable_capture(frame, x, y, w, h):
                        continue
                    enc = embed_face_bgr(frame, x, y, w, h, lmarks)
                    if enc is None:
                        continue
                    if self._too_similar_to_last_capture(enc):
                        continue
                    x1, y1, x2, y2 = self._expand_box(x, y, w, h, frame.shape[1], frame.shape[0], margin=0.35)
                    crop = frame[y1:y2, x1:x2]
                    crop = imutils.resize(crop, width=160)
                    sample_num += 1
                    cv2.imwrite(directory + '/' + str(sample_num) + '.jpg', crop)
                    cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
                    cv2.putText(frame, f"{sample_num}/{num_samples}", (x1, y1 - 10),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
                    if sample_num >= num_samples:
                        stop_event.set()
                    break
                with self.lock:
                    self.dataset_count = sample_num
                    self.status = f"در حال گرفتن عکس... ({sample_num}/{num_samples})"
            else:
                stop_event.set()
                break

            ok, jpeg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
            if ok:
                with self.lock:
                    self.frame_jpeg = jpeg.tobytes()

            time.sleep(0.03)

        stop_event.set()
        try:
            cap.release()
        except Exception:
            pass
        with self.lock:
            if self.session_token == my_token:
                self.active = False
                self.dataset_done = True
                self.frame_jpeg = None
                self.dataset_phase = 'done'
                self.status = f"تکمیل شد ({sample_num} عکس ذخیره شد)"
        print("[INFO] دوربین سرور بسته شد (دیتاست)")

        # دوربین بلافاصله آزاد می‌شود تا صف جلو برود؛ آموزش مدل بعد از آزاد شدن دوربین انجام می‌گیرد
        self.session_finished(my_ticket)

        if self.dataset_auto_train and sample_num > 0:
            self._auto_train_model(my_ticket)

    def get_dataset_status(self, ticket=None):
        """وضعیت ثبت عکس چهره — فقط برای صاحب نشست (وگرنه فقط heartbeat تمدید
        می‌شود و کاربر دیگر پیشرفت کاربر اول را نمی‌بیند)."""
        if ticket is not None:
            self.touch_heartbeat(ticket)
        with self.lock:
            if ticket is not None:
                entry = self.sessions.get(ticket)
                if entry is not None and entry['state'] == 'finishing':
                    snap = dict(entry.get('dataset_snapshot') or {})
                    snap['finished'] = True
                    return snap
            return self._dataset_snapshot_locked()

    def _auto_train_model(self, ticket=None):
        # فقط یک آموزش هم‌زمان: اگر آموزش دیگری در جریان است، همان کافی است
        if not self._train_lock.acquire(blocking=False):
            with self.lock:
                msg = "آموزش مدل هم‌اکنون توسط درخواست دیگری در حال انجام است..."
                self.dataset_train_status = msg
                self.dataset_train_done = True
                self.status = msg
                if ticket is not None:
                    entry = self.sessions.get(ticket)
                    if entry is not None:
                        dsnap = dict(entry.get('dataset_snapshot') or {})
                        dsnap['train_status'] = msg
                        dsnap['train_done'] = True
                        entry['dataset_snapshot'] = dsnap
            return
        try:
            self._auto_train_model_locked(ticket=ticket)
        finally:
            self._train_lock.release()

    def _auto_train_model_locked(self, ticket=None):
        with self.lock:
            self.status = "در حال آموزش مدل با عکس‌های جدید..."
            self.dataset_train_status = "در حال آموزش مدل..."
            if ticket is not None:
                entry = self.sessions.get(ticket)
                if entry is not None:
                    dsnap = dict(entry.get('dataset_snapshot') or {})
                    dsnap['train_status'] = self.dataset_train_status
                    entry['dataset_snapshot'] = dsnap
        print("[INFO] شروع آموزش خودکار مدل...")

        try:
            count, message = train_gallery()
            if count == 0:
                with self.lock:
                    msg = message
                    self.dataset_train_status = msg
                    self.status = msg
                    if ticket is not None:
                        entry = self.sessions.get(ticket)
                        if entry is not None:
                            dsnap = dict(entry.get('dataset_snapshot') or {})
                            dsnap['train_status'] = msg
                            dsnap['train_done'] = True
                            entry['dataset_snapshot'] = dsnap
                return

            self.load_model()

            with self.lock:
                msg = message + f" ({count} عکس)"
                self.dataset_train_status = msg
                self.status = msg
                self.dataset_train_done = True
                if ticket is not None:
                    entry = self.sessions.get(ticket)
                    if entry is not None:
                        dsnap = dict(entry.get('dataset_snapshot') or {})
                        dsnap['train_status'] = msg
                        dsnap['train_done'] = True
                        dsnap['finished'] = True
                        entry['dataset_snapshot'] = dsnap
            print(f"[INFO] آموزش خودکار مدل انجام شد ({count} عکس)")
        except Exception as e:
            with self.lock:
                msg = f"خطا در آموزش مدل: {e}"
                self.dataset_train_status = msg
                self.status = msg
                if ticket is not None:
                    entry = self.sessions.get(ticket)
                    if entry is not None:
                        dsnap = dict(entry.get('dataset_snapshot') or {})
                        dsnap['train_status'] = msg
                        dsnap['train_done'] = True
                        entry['dataset_snapshot'] = dsnap
            print(f"[ERROR] خطا در آموزش خودکار مدل: {e}")

    def _run_break_out(self, my_token, my_ticket):
        print("[INFO] در حال باز کردن وبکم سرور برای خروج موقت...")
        cap = self._open_camera()
        if not cap.isOpened():
            with self.lock:
                if self.session_token == my_token:
                    self.camera_error = camera_failure_message()
                    self.status = self.camera_error
                    self.active = False
            self.session_finished(my_ticket)
            return

        with self.lock:
            self.status = "لطفاً برای خروج موقت جلوی دوربین بایستید"

        start_time = time.time()
        TIMEOUT_SEC = 60
        last_process = 0.0

        stop_event = threading.Event()
        last_frame_time = [time.time()]
        self._start_watchdog(cap, stop_event, last_frame_time)

        while True:
            if stop_event.is_set():
                break

            with self.lock:
                if not self.active or self.session_token != my_token:
                    stop_event.set()
                    break
                heartbeat_age = time.time() - self.last_heartbeat

            if heartbeat_age > HEARTBEAT_TIMEOUT_SEC:
                with self.lock:
                    self.status = "اتصال با مرورگر قطع شد؛ دوربین آزاد شد"
                stop_event.set()
                break

            ret, frame = cap.read()
            if not ret or frame is None:
                time.sleep(0.05)
                continue

            last_frame_time[0] = time.time()
            fh, fw = frame.shape[:2]
            if fw != 640:
                frame = cv2.resize(frame, (640, 480), interpolation=cv2.INTER_LINEAR)

            with self.lock:
                self.last_raw_frame = frame.copy()

            now = time.time()
            if self.gallery_encodings is not None and now - last_process >= 0.15:
                last_process = now
                self._process_break_frame(frame)

            ok, jpeg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
            if ok:
                with self.lock:
                    self.frame_jpeg = jpeg.tobytes()

            with self.lock:
                done_now = self.done

            if done_now:
                time.sleep(2)
                break
            elif time.time() - start_time > TIMEOUT_SEC:
                with self.lock:
                    self.status = "زمان تمام شد. دوباره تلاش کنید."
                break

            time.sleep(0.03)

        stop_event.set()
        try:
            cap.release()
        except Exception:
            pass
        with self.lock:
            if self.session_token == my_token:
                self.active = False
                self.frame_jpeg = None
        self.session_finished(my_ticket)
        print("[INFO] دوربین سرور بسته شد (break out)")

    def _record_break_start(self, username, reason, frame):
        """شروع استراحت: یک رکورد باز (break_in خالی) می‌سازد و خروج موقت را
        در جدول Time ثبت می‌کند (همان رفتاری که از قبل وجود داشت)."""
        today = datetime.date.today()
        now = timezone.now()
        user = User.objects.get(username=username)
        Break(user=user, date=today, break_out=now, reason=reason or '').save()
        update_attendance_in_db_out([username], frame)

    def _record_break_end(self, username, frame):
        """پایان استراحت: رکوردِ بازِ امروز را می‌بندد و ورود را ثبت می‌کند.

        توجه: پیش از این اصلاح، «پایان استراحت» یک Break جدید با break_out=الان
        می‌ساخت و یک خروج در Time ثبت می‌کرد؛ یعنی کاربر عملاً «دوباره خارج»
        می‌شد و استراحت‌های امروز هرگز بسته نمی‌شدند.
        """
        today = datetime.date.today()
        now = timezone.now()
        user = User.objects.get(username=username)
        open_break = (Break.objects.filter(user=user, date=today, break_out__isnull=False,
                                            break_in__isnull=True)
                      .order_by('-break_out').first())
        if open_break is None:
            Break(user=user, date=today, break_in=now).save()
        else:
            open_break.break_in = now
            open_break.save(update_fields=['break_in'])
        update_attendance_in_db_in([username], frame)

    def _process_break_frame(self, frame):
        img_h, img_w = frame.shape[:2]

        for face in detect_faces_bgr(frame):
            x, y, w, h, lmarks = face
            if w < MIN_FACE_SIZE or h < MIN_FACE_SIZE:
                continue

            x1, y1 = max(x, 0), max(y, 0)
            x2, y2 = min(x + w, img_w), min(y + h, img_h)

            enc = embed_face_bgr(frame, x, y, w, h, lmarks)
            if enc is None:
                continue

            name, conf = self._classify(enc)
            if name:
                cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
                cv2.putText(frame, f"{name}", (x1, y1 - 10),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
                with self.lock:
                    if self.expected_username and name != self.expected_username:
                        self.status = f"این چهره با کاربر جاری ({self.expected_username}) همخوانی ندارد"
                        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 0, 255), 2)
                        cv2.putText(frame, "مجاز نیست", (x1, y1 - 30),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
                    else:
                        if name == self.last_name:
                            self.match_count += 1
                        else:
                            self.last_name = name
                            self.match_count = 1
                        self.status = f"شناسایی شد: {name} ({self.match_count}/{FACE_CONFIRM_COUNT})"
                        if self.match_count >= FACE_CONFIRM_COUNT and not self.done:
                            username = self.break_target_user
                            if self.mode == 'break_in':
                                self._record_break_end(username, frame)
                                self.status = f"بازگشت {username} از استراحت ثبت شد"
                            else:
                                self._record_break_start(username, self.break_reason, frame)
                                self.status = f"خروج موقت {username} ثبت شد"
                            self.done = True
                            self.marked_username = username
            else:
                cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 0, 255), 2)
                cv2.putText(frame, "ناشناس", (x1, y1 - 10),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)

    def _run_break_in(self, my_token, my_ticket):
        print("[INFO] در حال باز کردن وبکم سرور برای بازگشت...")
        cap = self._open_camera()
        if not cap.isOpened():
            with self.lock:
                if self.session_token == my_token:
                    self.camera_error = camera_failure_message()
                    self.status = self.camera_error
                    self.active = False
            self.session_finished(my_ticket)
            return

        with self.lock:
            self.status = "لطفاً برای بازگشت جلوی دوربین بایستید"

        start_time = time.time()
        TIMEOUT_SEC = 60
        last_process = 0.0

        stop_event = threading.Event()
        last_frame_time = [time.time()]
        self._start_watchdog(cap, stop_event, last_frame_time)

        while True:
            if stop_event.is_set():
                break

            with self.lock:
                if not self.active or self.session_token != my_token:
                    stop_event.set()
                    break
                heartbeat_age = time.time() - self.last_heartbeat

            if heartbeat_age > HEARTBEAT_TIMEOUT_SEC:
                with self.lock:
                    self.status = "اتصال با مرورگر قطع شد؛ دوربین آزاد شد"
                stop_event.set()
                break

            ret, frame = cap.read()
            if not ret or frame is None:
                time.sleep(0.05)
                continue

            last_frame_time[0] = time.time()
            fh, fw = frame.shape[:2]
            if fw != 640:
                frame = cv2.resize(frame, (640, 480), interpolation=cv2.INTER_LINEAR)

            with self.lock:
                self.last_raw_frame = frame.copy()

            now = time.time()
            if self.gallery_encodings is not None and now - last_process >= 0.15:
                last_process = now
                self._process_break_frame(frame)

            ok, jpeg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
            if ok:
                with self.lock:
                    self.frame_jpeg = jpeg.tobytes()

            with self.lock:
                done_now = self.done

            if done_now:
                time.sleep(2)
                break
            elif time.time() - start_time > TIMEOUT_SEC:
                with self.lock:
                    self.status = "زمان تمام شد. دوباره تلاش کنید."
                break

            time.sleep(0.03)

        stop_event.set()
        try:
            cap.release()
        except Exception:
            pass
        with self.lock:
            if self.session_token == my_token:
                self.active = False
                self.frame_jpeg = None
        self.session_finished(my_ticket)
        print("[INFO] break in recorded")


camera_manager = CameraManager()
