from django.db import migrations


def forwards_pending_status(apps, schema_editor):
    Invoice = apps.get_model('b2b_api', 'Invoice')
    Invoice.objects.filter(status='CREATED').update(status='PENDING')


def backwards_created_status(apps, schema_editor):
    Invoice = apps.get_model('b2b_api', 'Invoice')
    Invoice.objects.filter(status='PENDING').update(status='CREATED')


class Migration(migrations.Migration):
    dependencies = [
        ('b2b_api', '0007_promo_lifestyle_products'),
    ]

    operations = [
        migrations.RunPython(forwards_pending_status, backwards_created_status),
    ]
