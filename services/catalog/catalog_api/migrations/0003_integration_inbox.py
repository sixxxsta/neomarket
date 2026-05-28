from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('catalog_api', '0002_category_metadata')]

    operations = [
        migrations.CreateModel(
            name='IntegrationInbox',
            fields=[
                ('message_id', models.CharField(max_length=128, primary_key=True, serialize=False)),
                ('source', models.CharField(max_length=64)),
                ('event_type', models.CharField(max_length=64)),
                ('payload', models.JSONField(default=dict)),
                ('received_at', models.DateTimeField(auto_now_add=True)),
            ],
        ),
    ]
