"""مدل‌های اپ «گزارش کارکرد ماهیانه پروژه‌ها».

پروژه‌ها را ادمین تعریف می‌کند. کارمند هر روز ساعت کار خود را بین
پروژه‌ها تقسیم می‌کند و جمع ساعت هر روز نمی‌تواند از ساعت کار واقعی
(ثبت‌شده در سیستم حضور و غیاب) بیشتر شود.

ماه شمسی با `year`/`month` در `WorkLogMonth` نگه داشته می‌شود؛ تاریخ
هر رکورد در `WorkLog.date` میلادی است تا با بقیهٔ سیستم هم‌خوان باشد.
"""

from django.conf import settings
from django.db import models


class Project(models.Model):
    """یک پروژه/فعالیت شرکت که ادمین تعریف می‌کند."""

    name = models.CharField('نام پروژه', max_length=120, unique=True)
    code = models.CharField(
        'کد/نام اختصاری', max_length=60, blank=True, default='',
    )
    description = models.CharField('توضیح', max_length=300, blank=True, default='')
    is_active = models.BooleanField('فعال', default=True)
    sort_order = models.IntegerField('ترتیب نمایش', default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['sort_order', 'name']
        verbose_name = 'پروژه'
        verbose_name_plural = 'پروژه‌ها'

    def __str__(self):
        return self.name


class WorkLog(models.Model):
    """ساعت کار یک کاربر روی یک پروژه در یک روز مشخص."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name='کارمند',
        on_delete=models.CASCADE,
        related_name='worklogs',
    )
    date = models.DateField('تاریخ', db_index=True)
    project = models.ForeignKey(
        Project,
        verbose_name='پروژه',
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='worklogs',
    )
    # فقط وقتی project خالی است (ردیف «سایر») پر می‌شود.
    other_title = models.CharField(
        'عنوان سایر', max_length=120, blank=True, default='',
    )
    hours = models.DecimalField(
        'ساعت', max_digits=5, decimal_places=2, default=0,
    )
    note = models.CharField('یادداشت', max_length=300, blank=True, default='')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['date', 'project__sort_order', 'id']
        verbose_name = 'کارکرد پروژه'
        verbose_name_plural = 'کارکرد پروژه‌ها'
        indexes = [
            models.Index(fields=['user', 'date'], name='worklog_user_date_idx'),
            models.Index(fields=['project', 'date'], name='worklog_proj_date_idx'),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(hours__gte=0),
                name='worklog_hours_nonnegative',
            ),
        ]

    def __str__(self):
        return '%s — %s — %s' % (
            self.user.get_username(),
            self.date,
            self.title,
        )

    @property
    def title(self):
        if self.project_id:
            return self.project.name
        return (self.other_title or '').strip() or 'سایر'


class WorkLogMonth(models.Model):
    """وضعیت ماه شمسی برای هر کاربر (پیش‌نویس / ثبت‌شده)."""

    STATUS_DRAFT = 'draft'
    STATUS_SUBMITTED = 'submitted'
    STATUS_CHOICES = [
        (STATUS_DRAFT, 'پیش‌نویس'),
        (STATUS_SUBMITTED, 'ثبت‌شده'),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name='کارمند',
        on_delete=models.CASCADE,
        related_name='worklog_months',
    )
    year = models.PositiveSmallIntegerField('سال شمسی')
    month = models.PositiveSmallIntegerField('ماه شمسی')
    status = models.CharField(
        'وضعیت', max_length=12, choices=STATUS_CHOICES, default=STATUS_DRAFT,
    )
    submitted_at = models.DateTimeField('زمان ثبت نهایی', null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('user', 'year', 'month')
        ordering = ['-year', '-month']
        verbose_name = 'ماه کارکرد'
        verbose_name_plural = 'ماه‌های کارکرد'

    def __str__(self):
        return '%s — %s/%s — %s' % (
            self.user.get_username(), self.year, self.month,
            self.get_status_display(),
        )

    @property
    def is_submitted(self):
        return self.status == self.STATUS_SUBMITTED
