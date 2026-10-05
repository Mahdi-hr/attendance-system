# راه‌اندازی دوربین روی سرور (USB Webcam + VM)

## مشکل چیست؟

سرور فعلی یک **ماشین مجازی KVM** است و **هیچ دوربینی داخلش نیست**:

```
$ systemd-detect-virt   →  kvm
$ ls /dev/video*        →  وجود ندارد
$ lsusb                 →  فقط QEMU Tablet
```

سامانهٔ حضور و غیاب، دوربین را **سمت سرور** باز می‌کند (OpenCV روی `/dev/video*`).
بنابراین تا وقتی وبکم فیزیکی داخل این VM پاس داده نشود، صفحهٔ
`/register/photo/21/` با پیام «دوربین سرور پیدا نشد» مواجه می‌شود.

## راه‌حل: وبکم USB را داخل VM پاس بدهید (USB passthrough)

وبکم باید به‌صورت فیزیکی به **هاست** (ماشین فیزیکی که این VM روی آن اجرا می‌شود)
وصل شود و سپس به این VM داده شود:

### Proxmox VE (رایج‌ترین)

1. وبکم را به یک پورت USB هاست وصل کنید.
2. در Web UI: VM را انتخاب کنید → **Hardware** → **Add** → **USB Device**.
3. وبکم را از لیست انتخاب کنید (مثلاً «USB 2.0 Camera»).
4. **Restart** یا **Reboot** بزنید (hotplug هم معمولاً کار می‌کند).

### libvirt / virt-manager (توزیع‌های لینوکسی)

در virt-manager: VM → **Add Hardware** → **USB Host Device** → وبکم را انتخاب کنید.

یا با خط فرمان:

```bash
virsh attach-device <vm-name> usb-webcam.xml --live --config
```

با محتوای `usb-webcam.xml` (شناسه‌های ‌vendor:product را از `lsusb` روی هاست ببینید):

```xml
<hostdev mode='subsystem' type='usb'>
  <source>
    <vendor id='0x046d'/>
    <product id='0x085c'/>
  </source>
</hostdev>
```

## بعد از پاس‌دادن، این مراحل را انجام بدهید

```bash
cd /home/kian/Attendance

# 1) بررسی کن که دوربین داخل VM دیده شود:
./check_camera.sh
#    باید بگوید:  ✅ دوربین داخل VM دیده می‌شود  (/dev/video0)

# 2) کاربر سرویس را به گروه video اضافه کن (دسترسی به دوربین):
sudo usermod -aG video kian

# 3) فایل سرویس را با نسخهٔ به‌روزشده جایگزین کن (گروه video اضافه شده):
sudo cp attendance.service /etc/systemd/system/attendance.service
sudo systemctl daemon-reload

# 4) ری‌استارت سرویس (تا هم کد جدید بارگیری شود هم گروه video):
sudo systemctl restart attendance

# 5) وضعیت را ببین:
systemctl status attendance --no-pager
```

## چند دوربین؟ انتخاب دوربین پیش‌فرض

اگر بیش از یک وبکم دارید (یا دوربین به‌عنوان مثلاً `/dev/video1` شناسایی شد)،
یکی از این دو را به انتهای `/home/kian/Attendance/.env` اضافه کنید و سرویس را
ری‌استارت کنید:

```
CAMERA_DEVICE=/dev/video0     # مسیر دقیق دستگاه
# یا
CAMERA_INDEX=0                # شمارهٔ دستگاه
```

در صفحهٔ «ثبت عکس» هم می‌توانید دوربین را از لیست کشویی انتخاب کنید.

## اگر دوربین «در دسترس نیست» ولی /dev/video* هست

- مطمئن شوید کاربر `kian` عضو گروه `video` است (مرحلهٔ ۲ بالا) **و** سرویس بعدش
  ری‌استارت شده (گروه‌های فرایند هنگام start گرفته می‌شوند).
- مطمئن شوید برنامهٔ دیگری (مثل Zoom/Cheese یا همین سامانه) دوربین را قفل نکرده
  است — فقط یک فرایند می‌تواند V4L2 را باز کند.
- وبکم را یک بار از پورت USB جدا و دوباره وصل کنید.

## تست سریع بدون مرورگر

```bash
cd /home/kian/Attendance
venv/bin/python - <<'PY'
import cv2
cap = cv2.VideoCapture(0, cv2.CAP_V4L2)
print("باز شد؟", cap.isOpened())
ret, frame = cap.read()
print("فریم خوانده شد؟", ret, frame.shape if ret else "")
cap.release()
PY
```

اگر `باز شد؟ False` بود، با `check_camera.sh` و مراحل بالا مشکل را رفع کنید.