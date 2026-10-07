"""ممدل دادهٔ معموریت.

«معموریت» یعنی کارمند در یک بازهٔ زمانی مشخص به جای محل کار، مأموریت
بیرونی داشته است (بانک، دادگاه، مشتری، بازدید کارگاه و …).

چرخهٔ کار:
    ثبت توسط کارمند  →  «در انتظار بررسی»
    تأیید توسط ادمین →  «تأییدشده»  → از این لحظه روی گزارش اثر می‌گذارد
    رد توسط ادمین     →  «ردشده»     → کارمند می‌تواند ببیند چرا رد شده

نکتهٔ مهم: تا وقتی وضعیت «تأییدشده» نشده، این رکورد **هیچ اثری روی
ساعت کار، گزارش‌ها و خروجی اکسل ندارد**. این عمداً همان منطقی است که
برای «خطای خروج» در اپ users هم پیاده شده است.
"""

import datetime as dt

from django.conf import settings
from django.db import models
from django.utils import timezone


class ErrandQuerySet(models.QuerySet):
    """کوئری‌ست‌های پرکاربرد اپ معموریت."""

    def approved(self):
        """فقط معموریت‌های تأییدشده — این‌ها هستند که روی گزارش اثر دارند."""
        return self.filter(status=Errand.STATUS_APPROVED)

    def pending(self):
        return self.filter(status=Errand.STATUS_PENDING)

    def for_window(self, date_from=None, date_to=None):
        """معموریت‌هایی که با بازهٔ تاریخی تلاقی دارند.

        نکته: شرط روی `end_time` و `start_time` جداست تا معموریتی که از
        قبل از بازه شروع شده و داخل بازه ادامه دارد هم پیدا شود.
        """
        qs = self
        if date_to is not None:
            # شروع پیش از پایان بازه است؟
            qs = qs.filter(start_time__lt=_end_of_day(date_to))
        if date_from is not None:
            # پایان بعد از شروع بازه است؟
            qs = qs.filter(end_time__gt=_start_of_day(date_from))
        return qs

    def active_overlap(self, start, end, exclude_pk=None):
        """معموریت‌های همپوشان با یک بازه (برای جلوگیری از ثبت تکراری).

        همپوشانی وقتی رخ می‌دهد که `existing.start < end` و
        `existing.end > start`؛ معموریت‌هایی که دقیقاً به هم می‌چسبند
        (پایان یکی = شروع دیگری) همپوشانی نیستند.
        """
        qs = self.filter(start_time__lt=end, end_time__gt=start)
        # فقط وضعیت‌هایی که هنوز در جریان‌اند بررسی می‌شوند؛ یک درخواست
        # ردشده یا لغوشده مانع ثبت دوباره نیست.
        qs = qs.filter(status__in=[
            Errand.STATUS_PENDING,
            Errand.STATUS_APPROVED,
        ])
        if exclude_pk:
            qs = qs.exclude(pk=exclude_pk)
        return qs


def _start_of_day(date):
    """شروع بازهٔ یک روز به وقت تهران (aware)."""
    naive = dt.datetime.combine(date, dt.time.min)
    return timezone.make_aware(naive, timezone.get_current_timezone())


def _end_of_day(date):
    """شروع روزِ بعد — یعنی ان exclusive برای شرط `start_time__lt`."""
    return _start_of_day(date) + dt.timedelta(days=1)


class Errand(models.Model):
    """یک درخواست/ثبت معموریت با بازهٔ زمانی مشخص."""

    STATUS_PENDING = 'pending'
    STATUS_APPROVED = 'approved'
    STATUS_REJECTED = 'rejected'
    STATUS_CANCELLED = 'cancelled'

    STATUS_CHOICES = [
        (STATUS_PENDING, 'در انتظار بررسی'),
        (STATUS_APPROVED, 'تأییدشده'),
        (STATUS_REJECTED, 'ردشده'),
        (STATUS_CANCELLED, 'لغوشده توسط کارمند'),
    ]

    # ---------- چه کسی و کِی ----------
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='errands',
        verbose_name='کارمند',
    )

    # ---------- بازهٔ زمانی (aware، به وقت تهران) ----------
    start_time = models.DateTimeField(verbose_name='شروع معموریت')
    end_time = models.DateTimeField(verbose_name='پایان معموریت')

    # ---------- توضیحات ----------
    reason = models.CharField(
        max_length=500,
        verbose_name='دلیل/موضوع مأموریت',
        help_text='مثلاً: مراجعه به بانک برای کارتابل تسهیلات',
    )
    destination = models.CharField(
        max_length=200,
        blank=True,
        default='',
        verbose_name='مقصد',
        help_text='اختیاری — مثلاً: بانک ملت، شعبهٔ مرکزی',
    )
    contact = models.CharField(
        max_length=100,
        blank=True,
        default='',
        verbose_name='شمارهٔ تماس',
        help_text='اختیاری — برای مواقعی که لازم است در طول مأموریت تماس بگیرند',
    )

    # ---------- وضعیت و چرخهٔ تأیید ----------
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
        verbose_name='وضعیت',
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='reviewed_errands',
        verbose_name='بررسی‌کننده',
    )
    reviewed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='زمان بررسی',
    )
    review_note = models.CharField(
        max_length=500,
        blank=True,
        default='',
        verbose_name='یادداشت ادمین',
        help_text='به‌خصوص هنگام رد کردن: دلیل رد را اینجا بنویسید تا کارمند ببیند.',
    )

    created_at = models.DateTimeField(auto_now_add=True, verbose_name='زمان ثبت')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='آخرین تغییر')

    objects = ErrandQuerySet.as_manager()

    class Meta:
        verbose_name = 'معموریت'
        verbose_name_plural = 'معموریت‌ها'
        ordering = ['-start_time', '-id']
        indexes = [
            models.Index(fields=['user', 'start_time'], name='errand_user_start_idx'),
            models.Index(fields=['status', '-created_at'], name='errand_status_created_idx'),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(end_time__gt=models.F('start_time')),
                name='errand_end_after_start',
            ),
        ]

    def __str__(self):
        return '%s — %s تا %s' % (
            self.user.username,
            timezone.localtime(self.start_time).strftime('%Y-%m-%d %H:%M'),
            timezone.localtime(self.end_time).strftime('%H:%M'),
        )

    # ---------- محاسبات کمکی ----------

    @property
    def duration_seconds(self):
        return int((self.end_time - self.start_time).total_seconds())

    @property
    def duration_minutes(self):
        return int(self.duration_seconds // 60)

    def duration_display(self):
        """«۱۱ ساعت و ۳۰ دقیقه» با ارقام فارسی."""
        from recognition.reports import format_hours
        return format_hours(self.duration_seconds)

    def duration_compact(self):
        """شکل فشرده برای کارت‌های فهرست: «۱۱:۳۰»."""
        total = max(self.duration_minutes, 0)
        return '%d:%02d' % (total // 60, total % 60)

    @property
    def is_pending(self):
        return self.status == self.STATUS_PENDING

    @property
    def is_approved(self):
        return self.status == self.STATUS_APPROVED

    @property
    def is_rejected(self):
        return self.status == self.STATUS_REJECTED

    @property
    def is_cancelled(self):
        return self.status == self.STATUS_CANCELLED

    @property
    def is_open(self):
        """آیا همین الان داخل بازهٔ این معموریت است؟"""
        if not self.is_approved:
            return False
        now = timezone.now()
        return self.start_time <= now < self.end_time

    def is_in_past(self):
        return self.end_time <= timezone.now()

    # ---------- کنش‌های چرخهٔ تأیید ----------

    def approve(self, reviewer, note=''):
        """تأیید توسط ادمین. فقط درخواست‌های در انتظار قابل تأیید هستند."""
        if not self.is_pending:
            return False
        self.status = self.STATUS_APPROVED
        self.reviewed_by = reviewer
        self.reviewed_at = timezone.now()
        self.review_note = note or ''
        self.save(update_fields=[
            'status', 'reviewed_by', 'reviewed_at', 'review_note', 'updated_at',
        ])
        return True

    def reject(self, reviewer, note=''):
        """رد توسط ادمین."""
        if not self.is_pending:
            return False
        self.status = self.STATUS_REJECTED
        self.reviewed_by = reviewer
        self.reviewed_at = timezone.now()
        self.review_note = note or ''
        self.save(update_fields=[
            'status', 'reviewed_by', 'reviewed_at', 'review_note', 'updated_at',
        ])
        return True

    def cancel(self, by_user):
        """لغو توسط خود کارمند — فقط در انتظار بررسی."""
        if not self.is_pending:
            return False
        if by_user.pk != self.user_id and not by_user.is_superuser:
            return False
        self.status = self.STATUS_CANCELLED
        self.reviewed_at = timezone.now()
        self.save(update_fields=['status', 'reviewed_at', 'updated_at'])
        return True