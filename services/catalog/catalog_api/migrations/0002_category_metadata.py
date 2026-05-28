from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("catalog_api", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="category",
            name="description",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="category",
            name="image_url",
            field=models.URLField(blank=True),
        ),
        migrations.AddField(
            model_name="category",
            name="is_active",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="category",
            name="created_at",
            field=models.DateTimeField(auto_now_add=True, null=True),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="category",
            name="updated_at",
            field=models.DateTimeField(auto_now=True),
        ),
    ]