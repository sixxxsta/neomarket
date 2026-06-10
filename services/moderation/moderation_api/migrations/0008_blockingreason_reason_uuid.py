import uuid
from uuid import NAMESPACE_URL, uuid5

from django.db import migrations, models


def populate_reason_uuid(apps, schema_editor):
    BlockingReason = apps.get_model('moderation_api', 'BlockingReason')
    for reason in BlockingReason.objects.all():
        reason.reason_uuid = uuid5(NAMESPACE_URL, f'blocking-reason:{reason.code}')
        reason.save(update_fields=['reason_uuid'])


class Migration(migrations.Migration):
    dependencies = [('moderation_api', '0007_hard_blocked_queue_status')]

    operations = [
        migrations.AddField(
            model_name='blockingreason',
            name='reason_uuid',
            field=models.UUIDField(default=uuid.uuid4, editable=False, null=True),
        ),
        migrations.RunPython(populate_reason_uuid, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='blockingreason',
            name='reason_uuid',
            field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True, db_index=True),
        ),
    ]
