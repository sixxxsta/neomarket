from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("cart_api", "0003_b2c_cart_core"),
    ]

    operations = [
        migrations.AddField(
            model_name="banner",
            name="placement",
            field=models.CharField(
                choices=[("home", "home")],
                db_index=True,
                default="home",
                max_length=32,
            ),
        ),
    ]
