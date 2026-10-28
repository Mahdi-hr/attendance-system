"""مدل‌های اپ «بله بات».

هر کاربر بله یک رکورد `BaleAccount` دارد. اگر به حساب سامانه وصل شده باشد،
`django_user` پر می‌شود. کاربرانی که هم نقش «کاربر» و هم «مدیر» دارند
(`role == both`) می‌توانند با `active_role` بین دو حالت جابه‌جا شوند.
"""

from datetime import time

from django.conf import settings
from django.db import models


class BaleAccount(models.Model):
    """اتصال یک حساب بله به یک کاربر سامانه + وضعیت گفتگو."""

    ROLE_USER = 'user'
    ROLE_ADMIN = 'admin'
    ROLE_CHOICES = [
        (ROLE_USER, 'کاربر'),
        (ROLE_ADMIN, 'مدیر'),
    ]

    bale_user_id = models.BigIntegerField('شناسهٔ کاربر بله', unique=True)
    chat_id = models.BigIntegerField('شناسهٔ گفتگو', default=0)

    username = models.CharField('نام کاربری بله', max_length=100, blank=True, default='')
    first_name = models.CharField('نام', max_length=100, blank=True, default='')
    last_name = models.CharField('نام خانوادگی', max_length=100, blank=True, default='')

    django_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name='کاربر سامانه',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='bale_accounts',
    )

    # فقط برای کاربران «both» معنا دارد؛ بقیه همیشه نقش ثابت خود را دارند.
    active_role = models.CharField(
        'نقش فعال', max_length=10, choices=ROLE_CHOICES, default=ROLE_USER,
    )

    # وضعیت گفتگوی چندمرحله‌ای (ورود، ثبت مأموریت، رد کردن و …)
    state = models.CharField('وضعیت گفتگو', max_length=50, blank=True, default='')
    state_data = models.JSONField('دادهٔ موقت گفتگو', default=dict, blank=True)

    # محدودیت تلاش ورود ناموفق
    failed_attempts = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)

    is_blocked = models.BooleanField('مسدود', default=False)

    # ---------- ترجیحات اعلان ----------
    notify_errand = models.BooleanField('اعلان مأموریت', default=True)
    notify_daily = models.BooleanField('اعلان روزانه', default=True)
    notify_news = models.BooleanField('اخبار و اطلاعیه‌ها', default=True)

    # ---------- محدودیت نرخ پیام (ضد اسپم) ----------
    rate_window_start = models.DateTimeField(null=True, blank=True)
    rate_count = models.PositiveSmallIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    linked_at = models.DateTimeField(null=True, blank=True)
    last_seen = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'حساب بله'
        verbose_name_plural = 'حساب‌های بله'
        ordering = ['-last_seen']
        indexes = [
            models.Index(fields=['django_user'], name='baleaccount_user_idx'),
        ]

    def __str__(self):
        if self.django_user_id:
            return '%s (@%s)' % (
                self.django_user.get_username(), self.username or self.bale_user_id,
            )
        return 'بله %s' % (self.username or self.bale_user_id)

    @property
    def is_linked(self):
        return self.django_user_id is not None


class BotState(models.Model):
    """وضعیت سراسری ربات (مثلاً آخرین update پردازش‌شده)."""

    key = models.CharField(max_length=30, unique=True)
    last_update_id = models.BigIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'وضعیت ربات'
        verbose_name_plural = 'وضعیت ربات'

    def __str__(self):
        return '%s = %s' % (self.key, self.last_update_id)


class BotConfig(models.Model):
    """تنظیمات قابل‌تغییر ربات (تک‌رکوردی).

    عمداً در دیتابیس نگه داشته شده تا مدیر بتواند بدون دیپلوی، رفتار
    یادآورها و روشن/خاموش بودن ربات را عوض کند.
    """

    singleton_id = models.PositiveSmallIntegerField(
        primary_key=True, default=1, editable=False,
    )

    bot_enabled = models.BooleanField('ربات فعال باشد', default=True)
    reminders_enabled = models.BooleanField('یادآورها فعال باشند', default=True)

    work_end_time = models.TimeField('ساعت پایان کار', default=time(18, 0))
    checkout_reminder_after_minutes = models.PositiveIntegerField(
        'یادآور خروج بعد از چند دقیقه', default=60,
    )
    pending_digest_time = models.TimeField('ساعت یادآور صف تأیید', default=time(9, 0))
    upcoming_errand_minutes = models.PositiveIntegerField(
        'یادآور مأموریت چند دقیقه قبل', default=15,
    )
    broadcast_signature = models.CharField(
        'امضای پیام همگانی', max_length=100, blank=True,
        default='مدیریت سامانه',
    )

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'تنظیمات ربات بله'
        verbose_name_plural = 'تنظیمات ربات بله'

    def save(self, *args, **kwargs):
        self.singleton_id = 1
        super().save(*args, **kwargs)

    @classmethod
    def get_solo(cls):
        obj, _ = cls.objects.get_or_create(singleton_id=1)
        return obj

    def __str__(self):
        return 'تنظیمات ربات بله'


class AuditLog(models.Model):
    """ردپای اقدامات حساس (تأیید/رد، ساخت کاربر، ریست رمز، پیام همگانی…)."""

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name='انجام‌دهنده',
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='bale_audit_logs',
    )
    action = models.CharField('اقدام', max_length=40, db_index=True)
    target_type = models.CharField('نوع هدف', max_length=40, blank=True, default='')
    target_id = models.CharField('شناسهٔ هدف', max_length=60, blank=True, default='')
    details = models.TextField('جزئیات', blank=True, default='')
    created_at = models.DateTimeField('زمان', auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = 'ردپای اقدام'
        verbose_name_plural = 'ردپای اقدامات'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['action', '-created_at'], name='bale_audit_action_idx'),
        ]

    def __str__(self):
        return '%s — %s — %s' % (
            self.action,
            self.actor.get_username() if self.actor else 'سیستم',
            self.created_at,
        )


class ReminderLog(models.Model):
    """کلید یکتای هر یادآور ارسال‌شده تا روزی/رویدادی دوبار ارسال نشود."""

    key = models.CharField(max_length=120, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'یادآور ارسال‌شده'
        verbose_name_plural = 'یادآورهای ارسال‌شده'
        ordering = ['-created_at']

    def __str__(self):
        return self.key
