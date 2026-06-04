from django.db import migrations


def seed_reasons(apps, schema_editor):
    BlockingReason = apps.get_model('moderation_api', 'BlockingReason')
    defaults = [
        ('FORBIDDEN_CONTENT', 'Запрещенный контент', 'Контент нарушает правила площадки.'),
        ('MISLEADING_INFO', 'Недостоверное описание', 'Описание товара вводит покупателя в заблуждение.'),
        ('BAD_MEDIA', 'Некачественные фото', 'Изображения не соответствуют требованиям.'),
        ('WRONG_CATEGORY', 'Неверная категория', 'Товар размещен в неподходящей категории.'),
    ]
    for code, title, description in defaults:
        BlockingReason.objects.update_or_create(
            code=code,
            defaults={'title': title, 'description': description, 'is_active': True},
        )


def unseed_reasons(apps, schema_editor):
    BlockingReason = apps.get_model('moderation_api', 'BlockingReason')
    BlockingReason.objects.filter(
        code__in=['FORBIDDEN_CONTENT', 'MISLEADING_INFO', 'BAD_MEDIA', 'WRONG_CATEGORY']
    ).delete()


class Migration(migrations.Migration):
    dependencies = [('moderation_api', '0001_initial')]

    operations = [migrations.RunPython(seed_reasons, unseed_reasons)]
