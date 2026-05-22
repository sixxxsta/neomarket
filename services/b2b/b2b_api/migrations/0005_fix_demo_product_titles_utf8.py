# -*- coding: utf-8 -*-
from django.db import migrations


FIXES = {
    'f904e687-7946-4403-aa31-c78be1f5d879': 'Наушники Orion Pro',
    '147dc429-90b3-4235-b554-728011c1ccf7': 'Кофеварка Brew Master',
    'a51116e7-106d-4b90-a0c6-f9ce02544204': 'Йога-коврик Flex Mat',
    '5c8bea39-82f9-4062-b1f4-a9ca7f8c4d30': 'Рюкзак City Pack',
    '3f372a32-0d31-4be5-bf35-1167e26b0335': 'Конструктор Sky Blocks',
    '2ca4d32b-2ef3-46ed-a4ab-3995f779fe9b': 'Умная лампа Luma Home',
    'd368224b-ff56-4e4e-bc3a-3f7728779f05': 'Пылесос Clean Jet',
    '445e792a-0450-4d4b-8a29-2d0f85ee0f6b': 'Велошлем Trail Guard',
    '2c4b3355-a9fb-4015-808c-24a05416ab3e': 'Видеорегистратор Road Eye',
    '822575ff-b144-4f1b-a519-155d4da1df27': 'Книга «Маркетплейс 101»',
    'a8652f38-691a-4d76-bf84-ad53410157cb': 'Кроссовки Pulse Run',
    'f665fd16-a1d8-445e-b691-6bb4887ad3a4': 'Корм для кошек Cat Daily',
    '701265c8-c5eb-46b1-b690-a75b53537aaa': 'Набор кастрюль Steel Cook',
    '0d1c13b7-94f6-41c1-b1d1-66ac679274d0': 'Колонка Wave Mini',
    '25e72b12-4e6d-42c6-bedc-be4c5eb48ffd': 'Офисное кресло Ergo Seat',
}


def fix_titles(apps, schema_editor):
    Product = apps.get_model('b2b_api', 'Product')
    for pid, title in FIXES.items():
        Product.objects.filter(id=pid).update(title=title)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('b2b_api', '0004_b2b_quest_core'),
    ]

    operations = [
        migrations.RunPython(fix_titles, noop_reverse),
    ]
