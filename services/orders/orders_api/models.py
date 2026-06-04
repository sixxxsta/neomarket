import uuid

from django.core.validators import MinValueValidator
from django.db import models


class Order(models.Model):
    class Status(models.TextChoices):
        CREATED = "CREATED", "CREATED"
        PENDING = "PENDING", "PENDING"
        PAID = "PAID", "PAID"
        ASSEMBLING = "ASSEMBLING", "ASSEMBLING"
        DELIVERING = "DELIVERING", "DELIVERING"
        DELIVERED = "DELIVERED", "DELIVERED"
        CANCELED = "CANCELED", "CANCELED"
        CANCEL_PENDING = "CANCEL_PENDING", "CANCEL_PENDING"

    class PaymentMethod(models.TextChoices):
        CARD_ONLINE = "CARD_ONLINE", "CARD_ONLINE"
        CASH_ON_DELIVERY = "CASH_ON_DELIVERY", "CASH_ON_DELIVERY"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_id = models.UUIDField(db_index=True)
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.CREATED)
    number = models.CharField(max_length=32, blank=True, db_index=True)

    subtotal_amount = models.BigIntegerField(validators=[MinValueValidator(0)], default=0)
    delivery_cost = models.BigIntegerField(validators=[MinValueValidator(0)], default=0)
    total_amount = models.BigIntegerField(validators=[MinValueValidator(0)])
    address_id = models.UUIDField(null=True, blank=True, db_index=True)
    payment_method_id = models.UUIDField(null=True, blank=True, db_index=True)
    total_currency = models.CharField(max_length=8, default="RUB")

    payment_method = models.CharField(max_length=32, choices=PaymentMethod.choices)
    delivery_address = models.JSONField(default=dict)

    cancel_reason = models.CharField(max_length=500, null=True, blank=True)
    comment = models.CharField(max_length=500, null=True, blank=True)
    fulfilled_at = models.DateTimeField(null=True, blank=True, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]


class OrderItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey(Order, related_name="items", on_delete=models.CASCADE)

    product_id = models.UUIDField()
    sku_id = models.UUIDField()
    product_title = models.CharField(max_length=255, default="")
    sku_name = models.CharField(max_length=255, default="")
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])

    unit_price_amount = models.BigIntegerField(validators=[MinValueValidator(0)])
    unit_price_currency = models.CharField(max_length=8, default="RUB")

    line_total_amount = models.BigIntegerField(validators=[MinValueValidator(0)])
    line_total_currency = models.CharField(max_length=8, default="RUB")


class IdempotencyKey(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    key = models.CharField(max_length=255, unique=True)
    user_id = models.UUIDField(db_index=True)
    order = models.OneToOneField(Order, related_name="idempotency", on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)


class IntegrationOutbox(models.Model):
    id = models.BigAutoField(primary_key=True)
    aggregate_id = models.UUIDField(db_index=True)
    event_type = models.CharField(max_length=64)
    payload = models.JSONField(default=dict)
    published = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)


class IntegrationInbox(models.Model):
    message_id = models.CharField(max_length=128, primary_key=True)
    source = models.CharField(max_length=64)
    event_type = models.CharField(max_length=64)
    payload = models.JSONField(default=dict)
    received_at = models.DateTimeField(auto_now_add=True)
