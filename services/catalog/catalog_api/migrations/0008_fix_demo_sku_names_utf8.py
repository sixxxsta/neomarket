# -*- coding: utf-8 -*-
from django.db import migrations

PRODUCT_IDS = [
    'f904e687-7946-4403-aa31-c78be1f5d879',
    '147dc429-90b3-4235-b554-728011c1ccf7',
    'a51116e7-106d-4b90-a0c6-f9ce02544204',
    '5c8bea39-82f9-4062-b1f4-a9ca7f8c4d30',
    '3f372a32-0d31-4be5-bf35-1167e26b0335',
    '2ca4d32b-2ef3-46ed-a4ab-3995f779fe9b',
    'd368224b-ff56-4e4e-bc3a-3f7728779f05',
    '445e792a-0450-4d4b-8a29-2d0f85ee0f6b',
    '2c4b3355-a9fb-4015-808c-24a05416ab3e',
    '822575ff-b144-4f1b-a519-155d4da1df27',
    'a8652f38-691a-4d76-bf84-ad53410157cb',
    'f665fd16-a1d8-445e-b691-6bb4887ad3a4',
    '701265c8-c5eb-46b1-b690-a75b53537aaa',
    '0d1c13b7-94f6-41c1-b1d1-66ac679274d0',
    '25e72b12-4e6d-42c6-bedc-be4c5eb48ffd',
]


def fix_sku_names(apps, schema_editor):
    Product = apps.get_model('catalog_api', 'Product')
    Sku = apps.get_model('catalog_api', 'Sku')
    for pid in PRODUCT_IDS:
        product = Product.objects.filter(id=pid).first()
        if not product:
            continue
        title = (product.title or 'Товар').strip()[:160]
        skus = list(Sku.objects.filter(product_id=pid).order_by('id'))
        for idx, sku in enumerate(skus, start=1):
            sku.name = f'{title} · вариант {idx}' if len(skus) > 1 else f'{title} · основной SKU'
            sku.save(update_fields=['name'])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('catalog_api', '0007_fix_demo_product_titles_utf8'),
    ]

    operations = [
        migrations.RunPython(fix_sku_names, noop_reverse),
    ]
