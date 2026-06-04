import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("cart_api", "0002_bannerevent"),
    ]

    operations = [
        migrations.AddField(
            model_name="cartitem",
            name="product_id",
            field=models.UUIDField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="cartitem",
            name="unavailable_reason",
            field=models.CharField(
                blank=True,
                choices=[
                    ("OUT_OF_STOCK", "OUT_OF_STOCK"),
                    ("PRODUCT_BLOCKED", "PRODUCT_BLOCKED"),
                    ("PRODUCT_DELETED", "PRODUCT_DELETED"),
                    ("ON_MODERATION", "ON_MODERATION"),
                ],
                max_length=32,
                null=True,
            ),
        ),
        migrations.CreateModel(
            name="Banner",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("title", models.CharField(max_length=255)),
                ("image_url", models.CharField(max_length=500)),
                ("link", models.CharField(max_length=500)),
                ("priority", models.IntegerField(db_index=True, default=0)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("start_at", models.DateTimeField(blank=True, null=True)),
                ("end_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ["priority", "-created_at"]},
        ),
        migrations.CreateModel(
            name="Collection",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("title", models.CharField(max_length=255)),
                ("description", models.TextField(blank=True)),
                ("cover_image_url", models.CharField(blank=True, max_length=500)),
                ("target_url", models.CharField(blank=True, max_length=500)),
                ("priority", models.IntegerField(db_index=True, default=0)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("start_date", models.DateField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ["priority", "-created_at"]},
        ),
        migrations.CreateModel(
            name="ProductEventInbox",
            fields=[
                ("idempotency_key", models.CharField(max_length=128, primary_key=True, serialize=False)),
                ("event", models.CharField(max_length=64)),
                ("product_id", models.UUIDField(db_index=True)),
                ("sku_ids", models.JSONField(default=list)),
                ("reason", models.TextField(blank=True, null=True)),
                ("received_at", models.DateTimeField(auto_now_add=True)),
            ],
        ),
        migrations.CreateModel(
            name="CollectionProduct",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("product_id", models.UUIDField()),
                ("ordering", models.IntegerField(db_index=True, default=0)),
                (
                    "collection",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="collection_products", to="cart_api.collection"),
                ),
            ],
            options={"ordering": ["ordering", "product_id"]},
        ),
        migrations.AddConstraint(
            model_name="collectionproduct",
            constraint=models.UniqueConstraint(fields=("collection", "product_id"), name="uniq_collection_product"),
        ),
    ]
