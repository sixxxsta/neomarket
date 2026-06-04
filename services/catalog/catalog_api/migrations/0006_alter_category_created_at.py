from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("catalog_api", "0005_productimage_main_constraint"),
    ]

    operations = [
        migrations.AlterField(
            model_name="category",
            name="created_at",
            field=models.DateTimeField(auto_now_add=True),
        ),
    ]
