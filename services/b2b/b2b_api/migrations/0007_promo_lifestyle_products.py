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


def forwards(apps, schema_editor):
    Product = apps.get_model('b2b_api', 'Product')
    Sku = apps.get_model('b2b_api', 'Sku')
    Category = apps.get_model('b2b_api', 'Category')

    ref = Product.objects.filter(deleted=False).select_related('category').first()
    if not ref:
        return

    category, _ = Category.objects.get_or_create(
        id=LUX_CATEGORY_ID,
        defaults={'name': 'Люкс-предложения'},
    )
    seller_id = ref.seller_id

    for row in PROMO:
        if Product.objects.filter(id=row['id']).exists():
            continue
        images = [{'url': row['image'], 'alt': row['title'], 'ordering': 0}]
        product = Product(
            id=row['id'],
            seller_id=seller_id,
            title=row['title'],
            description=row['description'],
            status='MODERATED',
            category=category,
            images=images,
            characteristics=[],
            deleted=False,
        )
        product.save()
        Sku.objects.create(
            id=row['sku_id'],
            product=product,
            name='Стандарт',
            price=row['price'],
            cost_price=0,
            active_quantity=row['qty'],
            reserved_quantity=0,
            images=[],
            characteristics=[],
            deleted=False,
        )


def backwards(apps, schema_editor):
    Product = apps.get_model('b2b_api', 'Product')
    Sku = apps.get_model('b2b_api', 'Sku')
    Category = apps.get_model('b2b_api', 'Category')
    ids = [row['id'] for row in PROMO]
    Sku.objects.filter(product_id__in=ids).delete()
    Product.objects.filter(id__in=ids).delete()
    Category.objects.filter(id=LUX_CATEGORY_ID).delete()


class Migration(migrations.Migration):
    dependencies = [
        ('b2b_api', '0006_fix_demo_sku_names_utf8'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
