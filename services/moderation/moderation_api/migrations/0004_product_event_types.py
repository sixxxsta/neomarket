from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("moderation_api", "0003_event_bus_support"),
    ]

    operations = [
        migrations.AlterField(
            model_name="moderationcard",
            name="event_type",
            field=models.CharField(
                choices=[
                    ("CREATED", "CREATED"),
                    ("UPDATED", "UPDATED"),
                    ("EDITED", "EDITED"),
                ],
                max_length=16,
            ),
        ),
        migrations.AlterField(
            model_name="moderationcard",
            name="queue_status",
            field=models.CharField(
                choices=[
                    ("PENDING", "PENDING"),
                    ("IN_REVIEW", "IN_REVIEW"),
                    ("APPROVED", "APPROVED"),
                    ("DECLINED", "DECLINED"),
                    ("ARCHIVED", "ARCHIVED"),
                ],
                db_index=True,
                default="PENDING",
                max_length=16,
            ),
        ),
    ]
