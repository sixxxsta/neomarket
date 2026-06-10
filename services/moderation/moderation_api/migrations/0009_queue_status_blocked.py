from django.db import migrations, models


def migrate_declined_to_blocked(apps, schema_editor):
    ModerationCard = apps.get_model('moderation_api', 'ModerationCard')
    ModerationCard.objects.filter(queue_status='DECLINED').update(queue_status='BLOCKED')


class Migration(migrations.Migration):
    dependencies = [('moderation_api', '0008_blockingreason_reason_uuid')]

    operations = [
        migrations.AlterField(
            model_name='moderationcard',
            name='queue_status',
            field=models.CharField(
                choices=[
                    ('PENDING', 'PENDING'),
                    ('IN_REVIEW', 'IN_REVIEW'),
                    ('APPROVED', 'APPROVED'),
                    ('BLOCKED', 'BLOCKED'),
                    ('DECLINED', 'DECLINED'),
                    ('HARD_BLOCKED', 'HARD_BLOCKED'),
                    ('ARCHIVED', 'ARCHIVED'),
                ],
                db_index=True,
                default='PENDING',
                max_length=16,
            ),
        ),
        migrations.RunPython(migrate_declined_to_blocked, migrations.RunPython.noop),
    ]
