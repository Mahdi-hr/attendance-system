from django.db import migrations, models
import django.db.models.deletion
from django.conf import settings


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Errand',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('start_time', models.DateTimeField(verbose_name='شروع معموریت')),
                ('end_time', models.DateTimeField(verbose_name='پایان معموریت')),
                ('reason', models.CharField(
                    help_text='مثلاً: مراجعه به بانک برای کارتابل تسهیلات',
                    max_length=500, verbose_name='دلیل/موضوع مأموریت')),
                ('destination', models.CharField(
                    blank=True, default='',
                    help_text='اختیاری — مثلاً: بانک ملت، شعبهٔ مرکزی',
                    max_length=200, verbose_name='مقصد')),
                ('contact', models.CharField(
                    blank=True, default='',
                    help_text='اختیاری — برای مواقعی که لازم است در طول مأموریت تماس بگیرند',
                    max_length=100, verbose_name='شمارهٔ تماس')),
                ('status', models.CharField(
                    choices=[
                        ('pending', 'در انتظار بررسی'),
                        ('approved', 'تأییدشده'),
                        ('rejected', 'ردشده'),
                        ('cancelled', 'لغوشده توسط کارمند'),
                    ],
                    db_index=True, default='pending',
                    max_length=20, verbose_name='وضعیت')),
                ('reviewed_at', models.DateTimeField(
                    blank=True, null=True, verbose_name='زمان بررسی')),
                ('review_note', models.CharField(
                    blank=True, default='',
                    help_text='به‌خصوص هنگام رد کردن: دلیل رد را اینجا بنویسید تا کارمند ببیند.',
                    max_length=500, verbose_name='یادداشت ادمین')),
                ('created_at', models.DateTimeField(
                    auto_now_add=True, verbose_name='زمان ثبت')),
                ('updated_at', models.DateTimeField(
                    auto_now=True, verbose_name='آخرین تغییر')),
                ('reviewed_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='reviewed_errands',
                    to=settings.AUTH_USER_MODEL, verbose_name='بررسی‌کننده')),
                ('user', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='errands',
                    to=settings.AUTH_USER_MODEL, verbose_name='کارمند')),
            ],
            options={
                'verbose_name': 'معموریت',
                'verbose_name_plural': 'معموریت‌ها',
                'ordering': ['-start_time', '-id'],
            },
        ),
        migrations.AddIndex(
            model_name='errand',
            index=models.Index(
                fields=['user', 'start_time'],
                name='errand_user_start_idx'),
        ),
        migrations.AddIndex(
            model_name='errand',
            index=models.Index(
                fields=['status', '-created_at'],
                name='errand_status_created_idx'),
        ),
        migrations.AddConstraint(
            model_name='errand',
            constraint=models.CheckConstraint(
                check=models.Q(('end_time__gt', models.F('start_time'))),
                name='errand_end_after_start'),
        ),
    ]