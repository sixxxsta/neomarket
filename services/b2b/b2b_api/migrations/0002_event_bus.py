from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('b2b_api', '0001_initial')]

    operations = [
        migrations.CreateModel(
            name='IntegrationOutbox',
            fields=[
                ('id', models.BigAutoField(primary_key=True, serialize=False)),
                ('aggregate_id', models.UUIDField(db_index=True)),
                ('event_type', models.CharField(max_length=64)),
                ('payload', models.JSONField(default=dict)),
                ('published', models.BooleanField(db_index=True, default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
        ),
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
