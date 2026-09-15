from django.db import migrations

def update_timeout_defaults(apps, schema_editor):
    AutobuyJob = apps.get_model('core', 'AutobuyJob')
    AutobuyJob.objects.filter(limit_order_timeout_minutes=60).update(limit_order_timeout_minutes=15)

def reverse_timeout_defaults(apps, schema_editor):
    pass

class Migration(migrations.Migration):

    dependencies = [
        ('core', '0010_alter_autobuyjob_limit_order_timeout_minutes'),
    ]

    operations = [
        migrations.RunPython(update_timeout_defaults, reverse_timeout_defaults),
    ]
