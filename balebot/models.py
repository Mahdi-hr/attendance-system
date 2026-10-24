"""مدل‌های اپ «بله بات».

هر کاربر بله یک رکورد `BaleAccount` دارد. اگر به حساب سامانه وصل شده باشد،
`django_user` پر می‌شود. کاربرانی که هم نقش «کاربر» و هم «مدیر» دارند
(`role == both`) می‌توانند با `active_role` بین دو حالت جابه‌جا شوند.
"""

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
