from django.db import models
from django.contrib.auth.models import User

import datetime


class UserProfile(models.Model):
	"""نقش هر کاربر در سامانه.

	نقش‌ها:
	- user : فقط کاربر عادی
	- admin : فقط ادمین
	- both : کاربر عادی + ادمین (می‌تواند با دکمه به حالت ادمین برود)
	"""
	ROLE_USER = 'user'
	ROLE_ADMIN = 'admin'
	ROLE_BOTH = 'both'
	ROLE_CHOICES = [
		(ROLE_USER, 'کاربر عادی'),
		(ROLE_ADMIN, 'ادمین'),
		(ROLE_BOTH, 'هر دو (کاربر و ادمین)'),
	]

	user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
	role = models.CharField(max_length=10, choices=ROLE_CHOICES, default=ROLE_USER)

	# شناسهٔ داخلیِ دائمی برای مدل چهره.
	#
	# مدل تشخیص چهره عکس‌های هر کس را در پوشه‌ای به نام `face_uid` نگه می‌دارد،
	# نه به نام کاربری. به همین دلیل تغییر نام کاربری (مثلاً «1010») هیچ اثری
	# روی مدل ندارد و برعکس، مدل به نام کاربری وابسته نیست.
	#
	# این مقدار یک‌بار ساخته می‌شود و **نباید عوض شود**. اگر عوض شود، پوشهٔ
	# عکس‌های آن کاربر پیدا نمی‌شود و باید دوباره عکس بگیرد. در پنل ادمین
	# فقط‌خواندنی است.
	# قید یکتایی دیتابیسی عمداً گذاشته نشده: پروفایل‌های تازه با
	# face_uid خالی ساخته می‌شوند و چند مقدار خالی باید مجاز باشند.
	# یکتا بودن شناسهٔ واقعی در سطح برنامه و داخل یک تراکنش تضمین
	# می‌شود (users.faceid.ensure_face_uid).
	face_uid = models.CharField(max_length=16, blank=True, default='',
		db_index=True)

	class Meta:
		verbose_name = 'پروفایل کاربر'
		verbose_name_plural = 'پروفایل کاربران'

	def __str__(self):
		return '%s - %s' % (self.user.username, self.get_role_display())


class ExitReason(models.Model):
    title = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=0)
    is_system = models.BooleanField(default=False)

    class Meta:
        ordering = ['sort_order', 'title']
        verbose_name = 'دلیل خروج'
        verbose_name_plural = 'دلایل خروج'

    def __str__(self):
        return self.title


class Present(models.Model):
	user=models.ForeignKey(User,on_delete=models.CASCADE)
	date = models.DateField(default=datetime.date.today)
	present=models.BooleanField(default=False)

	class Meta:
		verbose_name = 'حضور روزانه'
		verbose_name_plural = 'حضورهای روزانه'
		indexes = [
			# فهرست پنل و گزارش‌ها همیشه «تاریخ + کاربر» هستند.
			models.Index(fields=['date', 'user'], name='present_date_user_idx'),
			models.Index(fields=['-date'], name='present_date_desc_idx'),
		]

	def __str__(self):
		return '%s - %s' % (self.user.username, self.date)

class Time(models.Model):
	user=models.ForeignKey(User,on_delete=models.CASCADE)
	date = models.DateField(default=datetime.date.today)
	time=models.DateTimeField(null=True,blank=True)
	out=models.BooleanField(default=False)
	exit_reason = models.ForeignKey(ExitReason, null=True, blank=True, on_delete=models.SET_NULL)
	exit_reason_text = models.CharField(max_length=255, blank=True, default='')

	class Meta:
		verbose_name = 'زمان ورود/خروج'
		verbose_name_plural = 'زمان‌های ورود/خروج'
		indexes = [
			# پرکاربردترین کوئری‌ها: «ورودهای یک روز»، «همهٔ رکوردهای یک روز»
			# و «رکوردهای این کاربر در این بازه».
			models.Index(fields=['date', 'user', 'out'], name='time_date_user_out_idx'),
			models.Index(fields=['-date'], name='time_date_desc_idx'),
			models.Index(fields=['user', '-date'], name='time_user_date_idx'),
		]

	def __str__(self):
		return '%s - %s - %s' % (self.user.username, self.date, self.time)


class CheckOutError(models.Model):
	"""خروج خودکاری که هنگام ۲۴:۰۰ برای کسی که خروج ثبت نکرده ساخته شده است.

	این رکورد به‌عنوان «خطای خروج» در پنل مدیریت نمایش داده می‌شود تا مدیر
	بتواند زمان‌/دلیل خروج را دستی اصلاح و رفع کند.
	"""
	STATUS_PENDING = 'pending'
	STATUS_RESOLVED = 'resolved'

	user = models.ForeignKey(User, on_delete=models.CASCADE)
	date = models.DateField()
	time_record = models.OneToOneField(Time, on_delete=models.CASCADE, related_name='checkout_error')
	auto_out_time = models.DateTimeField()
	status = models.CharField(max_length=20, default=STATUS_PENDING,
	                          choices=[(STATUS_PENDING, 'در انتظار بررسی'), (STATUS_RESOLVED, 'رفع‌شده')])
	note = models.CharField(max_length=500, blank=True, default='')
	resolved_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL,
	                                related_name='resolved_checkout_errors')
	resolved_at = models.DateTimeField(null=True, blank=True)
	created_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		unique_together = ('user', 'date')
		ordering = ['-date', '-created_at']
		verbose_name = 'خطای خروج'
		verbose_name_plural = 'خطاهای خروج'
		indexes = [
			# صف کاری داشبورد: «همهٔ خطاهای در انتظار».
			models.Index(fields=['status', '-date'], name='cerr_status_date_idx'),
		]

	def __str__(self):
		return f'خطای خروج {self.user.username} - {self.date}'

class Break(models.Model):
	user = models.ForeignKey(User, on_delete=models.CASCADE)
	date = models.DateField(default=datetime.date.today)
	break_out = models.DateTimeField(null=True, blank=True)
	break_in = models.DateTimeField(null=True, blank=True)
	reason = models.CharField(max_length=255, blank=True, default='')
	
	class Meta:
		ordering = ['-break_out']
		verbose_name = 'استراحت'
		verbose_name_plural = 'استراحت‌ها'
		indexes = [
			models.Index(fields=['-break_out'], name='break_out_desc_idx'),
			models.Index(fields=['date', 'user'], name='break_date_user_idx'),
		]
	
	def duration_minutes(self):
		if self.break_out and self.break_in:
			diff = self.break_in - self.break_out
			return int(diff.total_seconds() / 60)
		return 0
	
	def duration_display(self):
		mins = self.duration_minutes()
		h = mins // 60
		m = mins % 60
		if h > 0:
			return f"{h} ساعت {m} دقیقه"
		return f"{m} دقیقه"


class AttendanceSnapshot(models.Model):
	"""عکس لحظه‌ای که هنگام تأیید چهره (ورود یا خروج) ثبت می‌شود.

	هدف: بازبینی دستی مدیر برای اطمینان از درستی تشخیص چهره.
	برای هر کاربر فقط آخرین `SNAPSHOT_LIMIT_PER_USER` عکس نگه داشته می‌شود
	و قدیمی‌ترین آن‌ها هنگام ثبت عکس جدید حذف می‌گردد.
	"""
	EVENT_IN = 'in'
	EVENT_OUT = 'out'
	EVENT_CHOICES = [
		(EVENT_IN, 'ورود'),
		(EVENT_OUT, 'خروج'),
	]

	user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='attendance_snapshots')
	event = models.CharField(max_length=10, choices=EVENT_CHOICES, default=EVENT_IN)
	# مسیر نسبی زیر پوشهٔ snapshots، مثلاً: username/14050901_123045_123456_in.jpg
	photo = models.CharField(max_length=300)
	created_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		ordering = ['-created_at', '-id']
		verbose_name = 'عکس لحظه ورود/خروج'
		verbose_name_plural = 'عکس‌های لحظه ورود/خروج'
		indexes = [
			# نگهداری «آخرین N عکس هر کاربر» و فهرست کلی پنل.
			models.Index(fields=['-created_at'], name='snap_created_desc_idx'),
			models.Index(fields=['user', '-created_at'], name='snap_user_created_idx'),
		]

	def __str__(self):
		return '%s - %s - %s' % (
			self.get_event_display(), self.user.username,
			self.created_at.strftime('%Y-%m-%d %H:%M:%S') if self.created_at else '',
		)
	

