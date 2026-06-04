import uuid

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("cart_api", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="BannerEvent",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("banner_id", models.UUIDField(db_index=True)),
                ("user_id", models.UUIDField(blank=True, db_index=True, null=True)),
                ("event", models.CharField(choices=[("impression", "impression"), ("click", "click")], max_length=16)),
                ("timestamp", models.DateTimeField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
        ),
    ]
