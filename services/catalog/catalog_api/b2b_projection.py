from django.db import transaction
from django.utils.text import slugify

from .models import Category, Product, ProductAttribute, ProductImage, Sku


def _category_slug(category_id, name):
    base = slugify(name or "category", allow_unicode=True) or "category"
    return f"{base[:40]}-{str(category_id)[:8]}"


def _map_status(status):
    if status == "HARD_BLOCKED":
        return Product.Status.BLOCKED
    if status in Product.Status.values:
        return status
    return Product.Status.CREATED


def _normalize_attributes(items):
    result = {}
    for item in items or []:
        name = str(item.get("name") or "").strip()
        value = str(item.get("value") or "").strip()
        if name:
            result[name] = value
    return result


def upsert_category_from_b2b(category_data):
    category_id = category_data.get("id")
    if not category_id:
        return None
    defaults = {
        "name": category_data.get("name", "General"),
        "slug": _category_slug(category_id, category_data.get("name", "general")),
        "is_active": True,
    }
    category, _created = Category.objects.update_or_create(id=category_id, defaults=defaults)
    return category


@transaction.atomic
def sync_product_snapshot(snapshot):
    if not snapshot or not snapshot.get("id"):
        return None
    if snapshot.get("deleted"):
        Product.objects.filter(id=snapshot.get("id")).delete()
        return None

    category = upsert_category_from_b2b(snapshot.get("category") or {})
    if category is None:
        return None

    product, _created = Product.objects.update_or_create(
        id=snapshot["id"],
        defaults={
            "title": snapshot.get("title", ""),
            "description": snapshot.get("description", ""),
            "status": _map_status(snapshot.get("status")),
            "category": category,
        },
    )

    ProductAttribute.objects.filter(product=product).delete()
    ProductAttribute.objects.bulk_create(
        [
            ProductAttribute(product=product, name=name, value=value)
            for name, value in _normalize_attributes(snapshot.get("characteristics") or []).items()
        ]
    )

    ProductImage.objects.filter(product=product).delete()
    product_images = snapshot.get("images") or []
    ProductImage.objects.bulk_create(
        [
            ProductImage(
                product=product,
                image_url=image.get("url", ""),
                alt_text=image.get("alt", "") or "",
                is_main=index == 0,
                order=int(image.get("ordering", index) or index),
            )
            for index, image in enumerate(product_images)
            if image.get("url")
        ]
    )

    incoming_skus = snapshot.get("skus") or []
    incoming_ids = {str(item.get("id")) for item in incoming_skus if item.get("id")}
    product.skus.exclude(id__in=incoming_ids).delete()

    for sku in incoming_skus:
        sku_id = sku.get("id")
        if not sku_id:
            continue
        Sku.objects.update_or_create(
            id=sku_id,
            defaults={
                "product": product,
                "name": sku.get("name", ""),
                "price": int(sku.get("price") or 0),
                "active_quantity": int(sku.get("active_quantity") or 0),
                "attributes": _normalize_attributes(sku.get("characteristics") or []),
            },
        )

    return product
