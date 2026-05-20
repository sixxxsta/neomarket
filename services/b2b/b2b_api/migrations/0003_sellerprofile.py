from django.db import migrations, models
import uuid


class Migration(migrations.Migration):
    dependencies = [
        ('b2b_api', '0002_event_bus'),
    ]

    operations = [
        migrations.CreateModel(
            name='SellerProfile',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('seller_id', models.UUIDField(db_index=True, unique=True)),
                ('company_name', models.CharField(default='NeoMarket Seller', max_length=255)),
                ('contact_person', models.CharField(default='Команда продаж', max_length=255)),
                ('email', models.EmailField(blank=True, default='', max_length=254)),
                ('phone', models.CharField(blank=True, default='', max_length=64)),
                ('warehouse_id', models.UUIDField(default=uuid.uuid4)),
                ('rating', models.DecimalField(decimal_places=1, default=4.9, max_digits=3)),
                ('reviews', models.PositiveIntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={'ordering': ['seller_id']},
        ),
    ]
