# Generated manually for bootstrap

import uuid

from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name='Category',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=255)),
            ],
            options={'ordering': ['name']},
        ),
        migrations.CreateModel(
            name='Invoice',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('seller_id', models.UUIDField(db_index=True)),
                ('warehouse_id', models.UUIDField(db_index=True)),
                (
                    'status',
                    models.CharField(
                        choices=[('CREATED', 'CREATED'), ('ACCEPTED', 'ACCEPTED'), ('REJECTED', 'REJECTED')],
                        db_index=True,
                        default='CREATED',
                        max_length=16,
                    ),
                ),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('accepted_at', models.DateTimeField(blank=True, null=True)),
            ],
            options={'ordering': ['-created_at']},
        ),
        migrations.CreateModel(
            name='Product',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('seller_id', models.UUIDField(db_index=True)),
                ('title', models.CharField(max_length=255)),
                ('description', models.TextField(blank=True)),
                (
                    'status',
                    models.CharField(
                        choices=[
                            ('CREATED', 'CREATED'),
                            ('ON_MODERATION', 'ON_MODERATION'),
                            ('MODERATED', 'MODERATED'),
                            ('BLOCKED', 'BLOCKED'),
                        ],
                        db_index=True,
                        default='CREATED',
                        max_length=32,
                    ),
                ),
                ('images', models.JSONField(blank=True, default=list)),
                ('characteristics', models.JSONField(blank=True, default=list)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('category', models.ForeignKey(on_delete=models.deletion.PROTECT, related_name='products', to='b2b_api.category')),
            ],
            options={'ordering': ['-created_at']},
        ),
        migrations.CreateModel(
            name='Sku',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=255)),
                ('price', models.BigIntegerField()),
                ('active_quantity', models.IntegerField(default=0)),
                ('characteristics', models.JSONField(blank=True, default=list)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('product', models.ForeignKey(on_delete=models.deletion.CASCADE, related_name='skus', to='b2b_api.product')),
            ],
            options={'ordering': ['name']},
        ),
        migrations.CreateModel(
            name='InvoiceItem',
            fields=[
                ('id', models.BigAutoField(primary_key=True, serialize=False)),
                ('quantity', models.PositiveIntegerField()),
                ('invoice', models.ForeignKey(on_delete=models.deletion.CASCADE, related_name='items', to='b2b_api.invoice')),
                ('sku', models.ForeignKey(on_delete=models.deletion.PROTECT, related_name='invoice_items', to='b2b_api.sku')),
            ],
            options={'unique_together': {('invoice', 'sku')}},
        ),
    ]
