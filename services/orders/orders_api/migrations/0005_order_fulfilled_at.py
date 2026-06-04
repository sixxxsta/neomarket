from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("orders_api", "0004_order_new_api_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="fulfilled_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
    ]
