# سامانهٔ حضور و غیاب با تشخیص چهره

سامانهٔ تحت‌وب حضور و غیاب کارکنان با **تشخیص چهره** مبتنی بر Django که نهاد حضورش را از راه **دوربین** و **تشخیص چهرهٔ زنده** ثبت می‌کند. رابط کاربری کامل فارسی (RTL)، تقویم شمسی، داشبورد کارمند و مدیر، مدیریت کاربران/دلایل خروج/خطاهای خروج، خروجی اکسل و چاپ.

---

## فهرست
- [امکانات](#امکانات)
- [تکنولوژی‌ها](#تکنولوژیها)
- [ساختار پروژه](#ساختار-پروژه)
- [مدل‌های داده](#مدلهای-داده)
- [نقش‌ها و دسترسی‌ها](#نقشها-و-دسترسیها)
- [نصب و راه‌اندازی](#نصب-و-راهاندازی)
- [اجرا (توسعه و تولید)](#اجرا-توسعه-و-تولید)
- [نکتهٔ معماری دوربین (مهم)](#نکتهٔ-معماری-دوربین-مهم)
- [پنل مدیریت (Jazzmin)](#پنل-مدیریت-jazzmin)
- [مسیرهای اصلی (URLs)](#مسیرهای-اصلی-urls)
- [تست و چک سلامت](#تست-و-چک-سلامت)
- [عیب‌یابی](#عیبیابی)

---

## امکانات

### ورود و خروج (تشخیص چهره)
- ثبت ورود/خروج با **تشخیص چهره‌ی زنده** از دوربین در مرورگر (استریم + snap)
- اعلام **ورود/خروج همزمان** دو نفر به یکدیگر
- **استراحت** (شروع/پایان استراحت) با ثبت دلیل
- ثبت **دلیل خروج** هنگام خروج (از فهرست دلایل مدیریت‌شده یا متن آزاد)
- **بازگشت خودکار ساعت** در پایان شب برای افرادی که خروج ثبت نکرده‌اند + رکورد «خطای خروج» برای اصلاح دستی بعدی
- لغو ورود اشتباه، مشاهدهٔ وضعیت زندهٔ حضور در صفحهٔ حضور

### گزارش‌ها
- گزارش حضور کارمند به‌تفکیک تاریخ (بازهٔ دلخواه، امروز/هفتهٔ جاری/ماه جاری/۳۰ روز اخیر با پیش‌تنظیم)
- گزارش همهٔ کارمندان در یک تاریخ، و گزارش کارمند مشخص در بازه
- خلاصهٔ **هفتگی و ماهانه**، نمودار عمودی **ساعات حضور روزانه نسبت به روز کاری ۸ ساعته**
- آمار: روزهای حضور، مجموع ساعات، میانگین روزانه، بیشترین ساعاتِ یک روز
- خروجی **اکسل** و **نسخهٔ چاپ** برای هر سه نوع گزارش

### مدیریت
- مدیریت کامل کاربران (ثبت‌نام، گرفتن عکس، آموزش مدل، ویرایش، حذف، جستجو)
- صفحهٔ مدیریت **دلایل خروج** (افزودن/جابجایی/غیرفعال‌سازی/حذف)
- صفحهٔ **خطاهای خروج** برای اصلاح ساعت/دلیل خروج خودکار توسط مدیر
- **پنل ادمین جازمین** برای همهٔ اپ‌ها (کاربران، پروفایل/نقش، حضورها، زمان‌ها، استراحت‌ها، خطاها، دلایل خروج)
- **بازبینی عکس‌های لحظه ورود/خروج** (`/manage/snapshots/`) — هر بار که چهره کارمند در ورود/خروج تأیید شود، عکس لحظه ثبت و برای بازبینی دستی مدیر در دسترس می‌شود. برای هر کاربر فقط آخرین `SNAPSHOT_LIMIT_PER_USER` عکس نگهداری و قدیمی‌ترین‌ها خودکار حذف می‌شوند. امکان حذف دستی هم وجود دارد.

### رابط کاربری
- فارسی و RTL، تقویم **شمسی (جلالی)** در ورودی‌ها و گزارش‌ها
- تم تیره/روشن، ساعت و تاریخ زنده در هدر، واکنش‌گرا (موبایل/تبلت/دسکتاپ)

---

## تکنولوژی‌ها

| بخش | فناوری |
| --- | --- |
| وب‌فریمورک | Django 5.2 (Python 3.10–3.12 پیشنهادی) |
| تشخیص چهره | OpenCV (YuNet detector + ArcFace embedding از طریق DNN) + Python `face_recognition` (dlib) |
| پنل ادمین | django-jazzmin 3.0.5 |
| استریم ویدیو | MJPEG از طریق `video_feed` |
| پایگاه داده | SQLite (پیش‌فرض؛ قابل تعویض با PostgreSQL) |
| سرور تولید | Gunicorn + gthread + WhiteNoise |
| تقویم شمسی | jdatetime + فیلترهای به‌دست‌ساز `jalali_filters` |
| فرم‌ها | crispy-forms + crispy-bootstrap4 |

> لیست کامل و وابستگی‌های سیستم در `requirements.txt` آمده است (از جمله پیش‌نیازهای `apt` برای کامپایل dlib).

---

## ساختار پروژه

```
Attendance-For-Office/
├── manage.py
├── requirements.txt
├── README.md
├── CAMERA_SETUP.md
│
├── attendance_system_facial_recognition/   # تنظیمات Django
│   ├── settings.py                          # DEBUG/ALLOWED_HOSTS از متغیر محیط
│   ├── urls.py
│   └── wsgi.py
│
├── users/                                   # اپ کاربران و پروفایل/نقش
│   ├── models.py                            # UserProfile, Time, Present, Break,
│   │                                        #   CheckOutError, ExitReason
│   ├── views.py, roles.py, admin.py, context_processors.py
│   └── migrations/
│
├── recognition/                             # اپ اصلی (تشخیص چهره، حضور، گزارش)
│   ├── views.py                             # ورود/خروج، استراحت، گزارش‌ها، APIها
│   ├── utils.py, camera/ …                  # مدیریت دوربین و تشخیص چهره
│   ├── static/recognition/                  # CSS/JS (تم، jquery، تاریخ شمسی)
│   └── templates/
│       ├── base/base.html                   # قالب پایه (تم، هدر، ناوبری)
│       └── recognition/…                    # صفحات: داشبورد، حضور، گزارش، مدیریت
│
├── install_server.sh                        # نصب کامل سرور
├── run_dev.sh                               # اجرای توسعه (runserver)
├── run_server.sh                            # اجرای تولید (Gunicorn) — نسخهٔ systemd
├── run_gunicorn.sh
├── attendance.service                       # فایل سرویس systemd
├── open_firewall.sh / check_camera.sh
└── staticfiles/                             # خروجی collectstatic
```

---

## مدل‌های داده

| مدل | توضیح |
| --- | --- |
| `UserProfile` | نقش کاربر (user / admin / both) یک‌به‌یک با `User` |
| `Time` | یک رویداد ورود یا خروج: کاربر، تاریخ، زمان، صفر=ورود/یک=خروج، دلیل خروج |
| `Present` | وضعیت حضور روزانهٔ یک کاربر در یک تاریخ |
| `Break` | استراحت: شروع/پایان + دلیل |
| `CheckOutError` | خروج خودکارِ پایان شب برای بدون‌خروج‌ها؛ وضعیت pending/resolved + یادداشت |
| `ExitReason` | فهرست دلایل خروج (عنوان، فعال/غیرفعال، ترتیب، سیستمی) |

---

## نقش‌ها و دسترسی‌ها

سیستم سه نقش دارد (در `users/roles.py`):

| نقش | دسترسی |
| --- | --- |
| `user` | کاربر عادی — صفحهٔ حضور خودش، گزارش حضور خودش |
| `admin` | ادمین خالص — فقط پنل مدیریت و مشاهدهٔ گزارش کارکنان (بدون صفحهٔ حضور شخصی) |
| `both` | هر دو — به‌صورت پیش‌فرض کاربر عادی است و با دکمهٔ «حالت ادمین» به پنل مدیریت می‌رود (و برعکس) |

- منطق نقش در `users/context_processors.py` به همهٔ قالب‌ها تزریق می‌شود (`user_role`, `admin_mode`, `can_access_admin`).
- ویرایش نقش از صفحهٔ ویرایش کاربر (`/users/<id>/edit/`) و در پنل ادمین (جازمین) انجام می‌شود.
- بررسی دسترسی با `can_access_admin(request)`؛ کاربران بدون مجوز به `not_authorised` هدایت می‌شوند.

---

## نصب و راه‌اندازی

### ۱) وابستگی‌های سیستم (Debian/Ubuntu)
```bash
sudo apt install build-essential cmake git python3-venv python3-dev \
    libboost-all-dev libopenblas-dev liblapack-dev libjpeg-dev zlib1g-dev gfortran
```
> برای کامپایل `dlib/opencv` بهتر است Python 3.10 → 3.12 باشد (در پایتون‌های جدیدتر ممکن است نیاز به کامپایل دستی باشد).

### ۲) نصب پایتون
```bash
python3 -m venv venv
venv/bin/pip install --upgrade pip setuptools wheel
venv/bin/pip install -r requirements.txt
```

### ۳) اجرای اسکریپت نصب سرور
```bash
./install_server.sh
```
این اسکریپت نصب وابستگی‌ها، مایگریشن‌ها، `collectstatic`، ساخت ادمین پیش‌فرض و راه‌اندازی سرویس systemd را طبق متن خودش انجام می‌دهد (جزئیات داخل فایل).

---

## اجرا (توسعه و تولید)

### توسعه
```bash
./run_dev.sh            # runserver با DEBUG=1
```

### تولید (Gunicorn)
```bash
./run_server.sh         # Gunicorn روی 0.0.0.0:8000 (پیش‌فرض)
```

متغیرهای محیطی قابل تنظیم: `DJANGO_SECRET_KEY`, `DJANGO_DEBUG`, `DJANGO_ALLOWED_HOSTS`, `DJANGO_USE_HTTPS`, `HOST`, `PORT`, `GUNICORN_WORKERS`, `GUNICORN_THREADS`, `GUNICORN_TIMEOUT`.

### استقرار با systemd
```bash
sudo cp attendance.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now attendance
```
> اگر تغییر قالب/استاتیک دادید: `venv/bin/python manage.py collectstatic --noinput` را اجرا و سرویس را ری‌استارت کنید.

---

## نکتهٔ معماری دوربین (مهم)

وضعیت دوربین و تشخیص چهره (`camera_manager`) یک نمونهٔ **درون‌فرایندی** است و بین workerهای گونی‌کورن مشترک نیست. بنابراین:

- **`GUNICORN_WORKERS` باید ۱ باشد** و برای هم‌زمانی از `gthread + threads=8` استفاده شود (`+` به‌طور پیش‌فرض در `run_server.sh`).
- بیش از یک worker باعث به‌هم‌ریختن ورود/خروج و استریم دوربین می‌شود.

---

## پنل مدیریت (Jazzmin)

- مسیر: `/admin/`
- اپ‌های مدیریت‌شده: کاربران (با پروفایل/نقش به‌صورت inline و نشان رنگی)، پروفایل‌ها، زمان‌های ورود/خروج، حضورهای روزانه، استراحت‌ها، خطاهای خروج (با اکشن «علامت‌گذاری به‌عنوان رفع‌شده»)، دلایل خروج.
- تم `cyborg` + فونت **وزیرمتن** و استایل سفارشی در `recognition/static/recognition/css/jazzmin_admin.css`.
- نکته: پایدا استاتیک از نوع `CompressedStaticFilesStorage` استفاده می‌شود (نسخهٔ Manifest با جازمین ناسازگار بود و باعث خطای `Missing manifest entry for vendor/bootswatch` می‌شد).

---

## مسیرهای اصلی (URLs)

| مسیر | توضیح |
| --- | --- |
| `/` و `/dashboard/` | صفحهٔ اصلی و داشبورد |
| `/login/`, `/logout/`, `/register/` | ورود، خروج، ثبت‌نام کارمند |
| `/mark_your_attendance` | صفحهٔ ثبت ورود/خروج با دوربین |
| `/mark_your_attendance_out` | خروج از صفحهٔ حضور |
| `/mark_break_out/`, `/mark_break_in/`, `/break_status` | استراحت |
| `/video_feed/<mode>` | استریم MJPEG دوربین |
| `/users/` و `/users/<id>/edit/`, `/delete/` | مدیریت کاربران |
| `/profile/edit/`, `/profile/change_password/` | پروفایل خودم |
| `/view_attendance_home` و `_date` و `_employee` | گزارش‌های مدیر (+ `/export`, `/print`) |
| `/view_my_attendance` (+ `/export`, `/print`) | گزارش حضور من |
| `/checkout-errors/` (+ `/<id>/resolve/`) | خطاهای خروج |
| `/manage/exit-reasons/` | دلایل خروج |
| `/manage/snapshots/` (+ `/image/<id>/`, `/delete/<id>/`) | بازبینی و حذف عکس‌های لحظه ورود/خروج |
| `/admin/` | پنل مدیریت (جازمین) |
| `/admin-mode/on/`, `/admin-mode/off/` | سوییچ کاربرِ «both» به حالت ادمین |

---

## تنظیمات عکس‌های لحظه (Snapshots)

| متغیر محیط | پیش‌فرض | توضیح |
| --- | --- | --- |
| `SNAPSHOT_LIMIT_PER_USER` | `50` | حداکثر تعداد عکس نگهداری‌شده برای هر کاربر. با پر شدن، قدیمی‌ترین عکس حذف و عکس جدید جایگزین می‌شود. |

فایل‌ها در `face_recognition_data/snapshots/<username>/` ذخیره می‌شوند.
دسترسی تنها برای مدیران (نقش `admin` یا `both` در حالت ادمین).

---

## تست و چک سلامت

```bash
venv/bin/python manage.py check
venv/bin/python manage.py test recognition users
```
> تست‌ها با بک‌اند حافظه اجرا می‌شوند و به دادهٔ واقعی کار ندارند.

---

## عیب‌یابی

| مشکل | راه‌حل |
| --- | --- |
| «Missing staticfiles manifest entry for vendor/bootswatch» | از `CompressedStaticFilesStorage` استفاده شده؛ `collectstatic --noinput` بزنید. |
| ورود/خروج و دوربین به‌هم ریخته در حالت هم‌زمانی | `GUNICORN_WORKERS=1` و `GUNICORN_THREADS=8` (به‌طور پیش‌فرض تنظیم شده). |
| مدل چهره کند | `GUNICORN_TIMEOUT` را بالا ببرید (پیش‌فرض ۱۲۰). |
| دوربین کار نمی‌کند | `./check_camera.sh` را اجرا کنید و به `CAMERA_SETUP.md` مراجعه کنید. |
| تغییرات قالب/استاتیک در تولید اعمال نمی‌شود | `venv/bin/python manage.py collectstatic --noinput` سپس ری‌استارت سرویس. |

---

## به‌روزرسانی کد از گیت‌هاب (`update.sh`)

اسکریپت `update.sh` آخرین نسخهٔ کد را از گیت‌هاب می‌گیرد، **بدون دست‌زدن به داده‌های محلی**.

```bash
./update.sh --check    # فقط بگو چه چیزی قرار است عوض شود (بدون تغییر)
./update.sh            # آپدیت با پرسش و تأیید
./update.sh --yes      # بدون پرسش
./update.sh --reset    # تغییرات محلی کد را دور بریز، دقیقاً نسخهٔ سرور
./update.sh --no-restart
```

### چه چیزی به‌روز می‌شود و چه چیزی دست‌نخورده می‌ماند

| به‌روز می‌شود (فقط فایل‌های کد) | هرگز دست نمی‌خورد (داده‌های تو) |
| --- | --- |
| `recognition/`, `users/`, templates، static، اسکریپت‌ها | `db.sqlite3` (دیتابیس) |
| مایگریشن‌های جدید (با `migrate`) | `.env` (کلیدها و تنظیمات) |
| پکیج‌های جدید `requirements.txt` | `face_recognition_data/` (چهره‌ها، embeddings، مدل‌ها) |
| خروجی `collectstatic` | `staticfiles/`, `media/`, `venv/`, لاگ‌ها، فایل‌های untracked |

### تضمین‌های اسکریپت

1. **بکاپ خودکار داده‌ها** — پیش از هر مایگریشن، از دیتابیس با API رسمی sqlite
   (ایمن حتی هنگام اجرای سرویس) در `backups/<تاریخ>/` کپی می‌گیرد.
2. **تشخیص تداخل قبل از آپدیت** — اگر فایلی را هم تو تغییر داده باشی و هم در آپدیت
   جدید عوض شده باشد، آپدیت **متوقف** می‌شود و راه‌حل پیشنهاد می‌دهد؛
   هیچ conflict marker و هیچ خرابی‌ای پیش نمی‌آید.
3. **حفظ تغییرات محلی** — تغییراتت در `git stash` امن می‌ماند و بعد از آپدیت برمی‌گردد.
   فایل‌های untracked اصلاً جابه‌جا نمی‌شوند.
4. **فقط fast-forward** — با `--ff-only`؛ هیچ merge commit و هیچ بازنویسی تاریخچه.
5. **بازگشت خودکار** — خطا در هر مرحله (pip، migrate، …) باعث می‌شود کد به نسخهٔ قبل
   برگردد، دیتابیس از بکاپ بازیابی شود و سرویس ری‌استارت شود.
6. **قفل همزمانی** — دو آپدیت همزمان ممکن نیست (`.update.lock`).

### اولین نصب روی سیستم جدید

```bash
git clone https://github.com/Mahdi-hr/Attendance-For-Office.git
cd Attendance-For-Office

cp .env.example .env
python3 -c "import secrets; print(secrets.token_urlsafe(50))"   # در .env بگذار
sed -i "s|^DJANGO_SECRET_KEY=.*|DJANGO_SECRET_KEY=<کلید تولیدی>|" .env

python3 -m venv venv
venv/bin/pip install --upgrade pip setuptools wheel
venv/bin/pip install -r requirements.txt

venv/bin/python manage.py migrate
venv/bin/python manage.py createsuperuser
./run_server.sh
```

> 📌 `face_recognition_data/` در مخزن نیست (دادهٔ بیومتریک و فایل‌های حجیم مدل).
> باید جداگانه منتقل شود — بدون آن قابلیت تشخیص چهره کار نمی‌کند.
> مسیر پیش‌فرض مدل‌ها در `recognition/camera.py` تنظیم شده است.