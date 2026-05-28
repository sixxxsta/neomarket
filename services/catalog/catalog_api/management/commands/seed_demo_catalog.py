from django.core.management.base import BaseCommand
from django.db import transaction

from catalog_api.models import Category, Product, Sku


DEMO_CATEGORIES = [
    {
        "slug": "electronics",
        "name": "Электроника",
        "description": "Гаджеты, устройства и аксессуары",
        "children": [
            {"slug": "smartphones", "name": "Смартфоны", "description": "Телефоны и аксессуары"},
            {"slug": "audio", "name": "Аудио", "description": "Наушники, колонки, звук"},
        ],
    },
    {
        "slug": "fashion",
        "name": "Одежда",
        "description": "Повседневная и спортивная одежда",
        "children": [
            {"slug": "shoes", "name": "Обувь", "description": "Кроссовки и обувь"},
            {"slug": "outerwear", "name": "Верхняя одежда", "description": "Куртки и пальто"},
        ],
    },
    {
        "slug": "home",
        "name": "Дом",
        "description": "Все для уюта и быта",
        "children": [
            {"slug": "kitchen", "name": "Кухня", "description": "Посуда и техника"},
            {"slug": "decor", "name": "Декор", "description": "Освещение и украшения"},
        ],
    },
]

DEMO_PRODUCTS = [
    {
        "title": "Pulse X12 Smartphone",
        "description": "6.7 OLED, 256GB, eSIM",
        "category_slug": "smartphones",
        "status": Product.Status.MODERATED,
        "skus": [
            {"name": "Graphite 256GB", "price": 84990, "active_quantity": 15},
            {"name": "Blue 512GB", "price": 99990, "active_quantity": 8},
        ],
    },
    {
        "title": "Orbit Noise Canceling Headphones",
        "description": "ANC, 38h battery",
        "category_slug": "audio",
        "status": Product.Status.MODERATED,
        "skus": [
            {"name": "Black", "price": 18990, "active_quantity": 20},
            {"name": "Sand", "price": 19490, "active_quantity": 9},
        ],
    },
    {
        "title": "AeroRun Pro Sneakers",
        "description": "Легкие кроссовки для бега",
        "category_slug": "shoes",
        "status": Product.Status.MODERATED,
        "skus": [
            {"name": "42 EU", "price": 12990, "active_quantity": 17},
            {"name": "43 EU", "price": 12990, "active_quantity": 11},
        ],
    },
    {
        "title": "Nordic Down Jacket",
        "description": "Теплая куртка до -20C",
        "category_slug": "outerwear",
        "status": Product.Status.MODERATED,
        "skus": [
            {"name": "M / Navy", "price": 15990, "active_quantity": 12},
            {"name": "L / Olive", "price": 15990, "active_quantity": 6},
        ],
    },
    {
        "title": "ChefMaster Pan 28cm",
        "description": "Сковорода с антипригарным покрытием",
        "category_slug": "kitchen",
        "status": Product.Status.MODERATED,
        "skus": [
            {"name": "28cm", "price": 3490, "active_quantity": 40},
        ],
    },
    {
        "title": "Halo Smart Lamp",
        "description": "Умная лампа с управлением со смартфона",
        "category_slug": "decor",
        "status": Product.Status.MODERATED,
        "skus": [
            {"name": "Base", "price": 4990, "active_quantity": 23},
        ],
    },
    {
        "title": "Seller Draft Product",
        "description": "Товар только что создан и ожидает модерации",
        "category_slug": "smartphones",
        "status": Product.Status.ON_MODERATION,
        "skus": [
            {"name": "Draft SKU", "price": 20990, "active_quantity": 5},
        ],
    },
]


class Command(BaseCommand):
    help = "Seed demo categories, products, and SKUs for storefront showcase"

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true", help="Recreate demo data")

    @transaction.atomic
    def handle(self, *args, **options):
        force = options.get("force", False)
        if force:
            Sku.objects.all().delete()
            Product.objects.all().delete()
            Category.objects.all().delete()
            self.stdout.write(self.style.WARNING("Existing catalog data removed"))

        categories_by_slug = {}
        for root in DEMO_CATEGORIES:
            parent = self._upsert_category(root)
            categories_by_slug[root["slug"]] = parent
            for child in root.get("children", []):
                category = self._upsert_category(child, parent=parent)
                categories_by_slug[child["slug"]] = category

        created_products = 0
        for product_data in DEMO_PRODUCTS:
            category = categories_by_slug[product_data["category_slug"]]
            product, created = Product.objects.get_or_create(
                title=product_data["title"],
                defaults={
                    "description": product_data["description"],
                    "status": product_data["status"],
                    "category": category,
                },
            )
            if not created:
                product.description = product_data["description"]
                product.status = product_data["status"]
                product.category = category
                product.save(update_fields=["description", "status", "category", "updated_at"])
            else:
                created_products += 1

            existing = {sku.name: sku for sku in product.skus.all()}
            for sku_data in product_data["skus"]:
                sku = existing.get(sku_data["name"])
                if sku:
                    sku.price = sku_data["price"]
                    sku.active_quantity = sku_data["active_quantity"]
                    sku.save(update_fields=["price", "active_quantity", "updated_at"])
                else:
                    Sku.objects.create(product=product, **sku_data)

        self.stdout.write(self.style.SUCCESS(f"Demo catalog ready. Products created: {created_products}"))

    def _upsert_category(self, payload, parent=None):
        category, _ = Category.objects.get_or_create(
            slug=payload["slug"],
            defaults={
                "name": payload["name"],
                "description": payload.get("description", ""),
                "is_active": True,
                "parent": parent,
            },
        )
        category.name = payload["name"]
        category.description = payload.get("description", "")
        category.parent = parent
        category.is_active = True
        category.save(update_fields=["name", "description", "parent", "is_active", "updated_at"])
        return category
