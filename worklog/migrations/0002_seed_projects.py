"""پروژه‌های نمونهٔ اولیه (فقط اگر هیچ پروژه‌ای تعریف نشده باشد)."""

from django.db import migrations

SEED = [
    ('zabbix', 'zabbix', 1),
    ('attendance', 'attendance', 2),
    ('dcim', 'dcim', 3),
]


def seed_projects(apps, schema_editor):
    Project = apps.get_model('worklog', 'Project')
    if Project.objects.exists():
        return
    for name, code, order in SEED:
        Project.objects.create(name=name, code=code, sort_order=order)


class Migration(migrations.Migration):

    dependencies = [
        ('worklog', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(seed_projects, migrations.RunPython.noop),
    ]
