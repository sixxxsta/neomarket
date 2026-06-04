import uuid

from django.core.validators import MinValueValidator
from django.db import models


class Cart(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_id = models.UUIDField(null=True, blank=True, db_index=True)
    session_id = models.UUIDField(null=True, blank=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user_id"], name="uniq_cart_user_id"),
            models.UniqueConstraint(fields=["session_id"], name="uniq_cart_session_id"),
        ]


class CartItem(models.Model):
    class UnavailableReason(models.TextChoices):
        OUT_OF_STOCK = "OUT_OF_STOCK", "OUT_OF_STOCK"
        PRODUCT_BLOCKED = "PRODUCT_BLOCKED", "PRODUCT_BLOCKED"
        PRODUCT_DELETED = "PRODUCT_DELETED", "PRODUCT_DELETED"
        ON_MODERATION = "ON_MODERATION", "ON_MODERATION"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    cart = models.ForeignKey(Cart, related_name="items", on_delete=models.CASCADE)
    product_id = models.UUIDField(null=True, blank=True, db_index=True)
    sku_id = models.UUIDField(db_index=True)
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    unavailable_reason = models.CharField(max_length=32, choices=UnavailableReason.choices, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["cart", "sku_id"], name="uniq_cart_sku"),
        ]


class Favorite(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_id = models.UUIDField(db_index=True)
    product_id = models.UUIDField(db_index=True)
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user_id", "product_id"], name="uniq_user_product_favorite"),
        ]


class Subscription(models.Model):
    class NotifyEvent(models.TextChoices):
        IN_STOCK = "IN_STOCK", "IN_STOCK"
        PRICE_DOWN = "PRICE_DOWN", "PRICE_DOWN"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_id = models.UUIDField(db_index=True)
    product_id = models.UUIDField(db_index=True)
    notify_on = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user_id", "product_id"], name="uniq_user_product_subscription"),
        ]


class BannerEvent(models.Model):
    class EventType(models.TextChoices):
        IMPRESSION = "impression", "impression"
        CLICK = "click", "click"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    banner_id = models.UUIDField(db_index=True)
    user_id = models.UUIDField(null=True, blank=True, db_index=True)
    event = models.CharField(max_length=16, choices=EventType.choices)
    timestamp = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)


class Banner(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=255)
    image_url = models.CharField(max_length=500)
    link = models.CharField(max_length=500)
    priority = models.IntegerField(default=0, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)
    start_at = models.DateTimeField(null=True, blank=True)
    end_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["priority", "-created_at"]


class Collection(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    cover_image_url = models.CharField(max_length=500, blank=True)
    target_url = models.CharField(max_length=500, blank=True)
    priority = models.IntegerField(default=0, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)
    start_date = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["priority", "-created_at"]


class CollectionProduct(models.Model):
    collection = models.ForeignKey(Collection, related_name="collection_products", on_delete=models.CASCADE)
    product_id = models.UUIDField()
    ordering = models.IntegerField(default=0, db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["collection", "product_id"], name="uniq_collection_product"),
        ]
        ordering = ["ordering", "product_id"]


class ProductEventInbox(models.Model):
    idempotency_key = models.CharField(max_length=128, primary_key=True)
    event = models.CharField(max_length=64)
    product_id = models.UUIDField(db_index=True)
    sku_ids = models.JSONField(default=list)
    reason = models.TextField(null=True, blank=True)
    received_at = models.DateTimeField(auto_now_add=True)
