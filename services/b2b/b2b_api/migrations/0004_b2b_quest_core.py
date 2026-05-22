from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('b2b_api', '0003_sellerprofile')]

    operations = [
        migrations.AddField(
            model_name='product',
            name='deleted',
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.AddField(
            model_name='product',
            name='blocking_reason',
            field=models.JSONField(blank=True, default=None, null=True),
        ),
        migrations.AddField(
            model_name='product',
            name='field_reports',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AlterField(
            model_name='product',
            name='status',
            field=models.CharField(
                choices=[
                    ('CREATED', 'CREATED'),
                    ('ON_MODERATION', 'ON_MODERATION'),
                    ('MODERATED', 'MODERATED'),
                    ('BLOCKED', 'BLOCKED'),
                    ('HARD_BLOCKED', 'HARD_BLOCKED'),
                ],
                db_index=True,
                default='CREATED',
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name='sku',
            name='cost_price',
            field=models.BigIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='sku',
            name='reserved_quantity',
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name='sku',
            name='images',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='sku',
            name='deleted',
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.CreateModel(
            name='InventoryOperation',
            fields=[
                ('key', models.CharField(max_length=128, primary_key=True, serialize=False)),
                (
                    'kind',
                    models.CharField(
                        choices=[('RESERVE', 'RESERVE'), ('UNRESERVE', 'UNRESERVE'), ('FULFILL', 'FULFILL')],
                        max_length=16,
                    ),
                ),
                ('payload', models.JSONField(default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
        ),
    ]
