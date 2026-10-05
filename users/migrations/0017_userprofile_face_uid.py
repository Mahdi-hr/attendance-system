"""به هر کاربر یک شناسهٔ داخلیِ دائمی می‌دهد.

این شناسه نام پوشهٔ عکس‌ها و برچسبِ مدل تشخیص چهره می‌شود. کارِ این
مهاجرت فقط «ساختن» شناسه است؛ جابه‌جایی پوشه‌ها و تغییر نام‌ها کارِ
دستور `migrate_face_uids` است که جداگانه اجرا می‌شود.

ترتیب عملیات مهم است: چند سطر با مقدار خالی نمی‌توانند هم‌زمان
`unique=True` داشته باشند، پس اول فیلد بدون قید یکتایی اضافه و پر
می‌شود، و بعد قید یکتایی اعمال می‌گردد.
"""
import uuid

from django.db import migrations, models

PREFIX = 'u'
HEX_LEN = 12


def _new_uid(taken):
    while True:
        candidate = PREFIX + uuid.uuid4().hex[:HEX_LEN]
        if candidate not in taken:
            return candidate


def assign_face_uids(apps, schema_editor):
    """به هر پروفایل یک شناسهٔ یکتا می‌دهد."""
    UserProfile = apps.get_model('users', 'UserProfile')
    taken = set(UserProfile.objects.exclude(face_uid='')
                .values_list('face_uid', flat=True))
    for profile in UserProfile.objects.filter(face_uid=''):
        uid = _new_uid(taken)
        taken.add(uid)
        profile.face_uid = uid
        profile.save(update_fields=['face_uid'])


def profiles_for_every_user(apps, schema_editor):
    """کاربری که پروفایل ندارد هم باید شناسه بگیرد."""
    UserProfile = apps.get_model('users', 'UserProfile')
    User = apps.get_model('auth', 'User')
    taken = set(UserProfile.objects.exclude(face_uid='')
                .values_list('face_uid', flat=True))
    existing = set(UserProfile.objects.values_list('user_id', flat=True))
    for user in User.objects.exclude(pk__in=existing):
        uid = _new_uid(taken)
        taken.add(uid)
        UserProfile.objects.create(user_id=user.pk, face_uid=uid)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0016_attendancesnapshot'),
    ]

    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='face_uid',
            field=models.CharField(blank=True, default='', max_length=16),
        ),
        migrations.RunPython(assign_face_uids, noop_reverse),
        migrations.RunPython(profiles_for_every_user, noop_reverse),
        migrations.AlterField(
            model_name='userprofile',
            name='face_uid',
            field=models.CharField(blank=True, default='', max_length=16,
                                   unique=True),
        ),
    ]