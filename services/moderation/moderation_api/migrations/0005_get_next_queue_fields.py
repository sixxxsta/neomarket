from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("moderation_api", "0004_product_event_types"),
    ]

    operations = [
        migrations.AddField(
            model_name="moderationcard",
            name="priority_queue",
            field=models.PositiveSmallIntegerField(db_index=True, default=1),
        ),
        migrations.AddField(
            model_name="moderationcard",
            name="review_started_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
    ]
