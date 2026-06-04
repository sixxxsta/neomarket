# -*- coding: utf-8 -*-
import uuid

from django.db import migrations


LUX_CATEGORY_ID = uuid.UUID('c411d000-1010-4a10-b200-000000000001')

PROMO = [
    {
        'id': uuid.UUID('b320c101-1010-4a10-b101-000000000001'),
        'sku_id': uuid.UUID('b320c102-1010-4a10-b101-000000000001'),
        'title': 'Частный самолёт NeoSky',
        'description': 'Дальний перелёт, салон под ключ, демо-карточка для витрины NeoMarket.',
        'price': 450_000_000,
        'qty': 2,
        'image': 'https://images.unsplash.com/photo-1436491865332-7a61a109cc05?auto=format&fit=crop&w=1200&q=80',
    },
    {
        'id': uuid.UUID('b320c101-1010-4a10-b102-000000000002'),
        'sku_id': uuid.UUID('b320c102-1010-4a10-b102-000000000002'),
        'title': 'Спорткар NeoSpeed',
        'description': 'Премиальный спортивный автомобиль, демо-карточка для витрины NeoMarket.',
        'price': 28_500_000,
        'qty': 3,
        'image': 'https://images.unsplash.com/photo-1494976388531-d1058494cdd8?auto=format&fit=crop&w=1200&q=80',
    },
    {
        'id': uuid.UUID('b320c101-1010-4a10-b103-000000000003'),
        'sku_id': uuid.UUID('b320c102-1010-4a10-b103-000000000003'),
        'title': 'Загородная резиденция NeoVilla',
        'description': 'Представительский дом с участком, демо-карточка для витрины NeoMarket.',
        'price': 320_000_000,
        'qty': 1,
        'image': 'https://images.unsplash.com/photo-1568605114967-8130f3a36994?auto=format&fit=crop&w=1200&q=80',
    },
    {
        'id': uuid.UUID('b320c101-1010-4a10-b104-000000000004'),
        'sku_id': uuid.UUID('b320c102-1010-4a10-b104-000000000004'),
        'title': 'Вертолёт NeoLift',
        'description': 'Однороторный вертолёт для частных перевозок, демо-карточка для витрины NeoMarket.',
        'price': 185_000_000,
        'qty': 1,
        'image': 'https://images.unsplash.com/photo-1587474260584-136574528ed5?auto=format&fit=crop&w=1200&q=80',
    },
]


def _snapshot(row):
    return {
        'id': str(row['id']),
        'title': row['title'],
        'description': row['description'],
        'status': 'MODERATED',
        'deleted': False,
        'category': {'id': str(LUX_CATEGORY_ID), 'name': 'Люкс-предложения'},
        'images': [{'url': row['image'], 'alt': row['title'], 'ordering': 0}],
        'characteristics': [],
        'skus': [
            {
                'id': str(row['sku_id']),
                'name': 'Стандарт',
                'price': row['price'],
                'active_quantity': row['qty'],
                'characteristics': [],
            }
        ],
    }


def forwards(apps, schema_editor):
    from catalog_api.b2b_projection import sync_product_snapshot

    for row in PROMO:
        sync_product_snapshot(_snapshot(row))


def backwards(apps, schema_editor):
    Product = apps.get_model('catalog_api', 'Product')
    ids = [row['id'] for row in PROMO]
    Product.objects.filter(id__in=ids).delete()


class Migration(migrations.Migration):
    dependencies = [
        ('catalog_api', '0008_fix_demo_sku_names_utf8'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
