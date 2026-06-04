from django.db import migrations, models


def mark_hard_only_reasons(apps, schema_editor):
    BlockingReason = apps.get_model('moderation_api', 'BlockingReason')
    BlockingReason.objects.filter(code='FORBIDDEN_CONTENT').update(hard_only=True)


class Migration(migrations.Migration):
    dependencies = [('moderation_api', '0005_get_next_queue_fields')]

    operations = [
        migrations.AddField(
            model_name='blockingreason',
            name='hard_only',
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(mark_hard_only_reasons, migrations.RunPython.noop),
    ]
