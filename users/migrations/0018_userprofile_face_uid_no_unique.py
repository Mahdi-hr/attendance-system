"""قید یکتایی دیتابیسی را از ``face_uid`` برمی‌دارد.

چرا
===
پروفایل‌های تازه با ``face_uid`` خالی ساخته می‌شوند و بعداً مقدار می‌گیرند.
با قید ``unique=True`` چند مقدار خالی با هم تضاد می‌کنند و ساختِ دومین
کاربر جدید می‌شکند. یکتا بودن شناسهٔ واقعی در سطح برنامه و داخل یک تراکنش
تضمین می‌شود (``users.faceid.ensure_face_uid``)، پس قید دیتابیسی لازم
نیست و فقط دردسرساز است.

همچنین یکتا بودن شناسه‌های موجود را بررسی و در صورت تکراری بودن اصلاح می‌کند.
"""
from django.db import migrations, models


def dedupe_face_uids(apps, schema_editor):
    """اگر شناسه‌ای بین چند پروفایل تکراری شده، به یکی از آن‌ها تازه می‌دهد."""
    UserProfile = apps.get_model('users', 'UserProfile')
    import uuid
    seen = set()
    for profile in UserProfile.objects.all().order_by('pk'):
        uid = profile.face_uid
        if not uid or uid in seen:
            fresh = 'u' + uuid.uuid4().hex[:12]
            while UserProfile.objects.exclude(pk=profile.pk).filter(
                    face_uid=fresh).exists():
                fresh = 'u' + uuid.uuid4().hex[:12]
            profile.face_uid = fresh
            profile.save(update_fields=['face_uid'])
            uid = fresh
        seen.add(uid)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0017_userprofile_face_uid'),
    ]

    operations = [
        migrations.AlterField(
            model_name='userprofile',
            name='face_uid',
            field=models.CharField(blank=True, db_index=True, default='',
                                   max_length=16),
        ),
        migrations.RunPython(dedupe_face_uids, noop_reverse),
    ]