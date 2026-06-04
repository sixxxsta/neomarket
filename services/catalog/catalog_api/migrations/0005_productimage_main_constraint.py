from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    dependencies = [
        ("catalog_api", "0004_new_models"),
    ]

    operations = [
        migrations.AlterUniqueTogether(
            name="productimage",
            unique_together=set(),
        ),
        migrations.AddConstraint(
            model_name="productimage",
            constraint=models.UniqueConstraint(
                fields=("product",),
                condition=Q(is_main=True),
                name="uniq_main_image_per_product",
            ),
        ),
    ]
