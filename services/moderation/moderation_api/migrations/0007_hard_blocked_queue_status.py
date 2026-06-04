from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('moderation_api', '0006_blocking_reason_hard_only')]

    operations = [
        migrations.AlterField(
            model_name='moderationcard',
            name='queue_status',
            field=models.CharField(
                choices=[
                    ('PENDING', 'PENDING'),
                    ('IN_REVIEW', 'IN_REVIEW'),
                    ('APPROVED', 'APPROVED'),
                    ('DECLINED', 'DECLINED'),
                    ('HARD_BLOCKED', 'HARD_BLOCKED'),
                    ('ARCHIVED', 'ARCHIVED'),
                ],
                db_index=True,
                default='PENDING',
                max_length=16,
            ),
        ),
    ]
