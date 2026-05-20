import uuid

from django.db import models


def default_profile_company():
    return 'NeoMarket Seller'


def default_profile_contact():
    return 'Команда продаж'


class Category(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class Product(models.Model):
    class Status(models.TextChoices):
        CREATED = 'CREATED', 'CREATED'
        ON_MODERATION = 'ON_MODERATION', 'ON_MODERATION'
        MODERATED = 'MODERATED', 'MODERATED'
        BLOCKED = 'BLOCKED', 'BLOCKED'
        HARD_BLOCKED = 'HARD_BLOCKED', 'HARD_BLOCKED'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    seller_id = models.UUIDField(db_index=True)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.CREATED, db_index=True)
    category = models.ForeignKey(Category, related_name='products', on_delete=models.PROTECT)
    images = models.JSONField(default=list, blank=True)
    characteristics = models.JSONField(default=list, blank=True)
    deleted = models.BooleanField(default=False, db_index=True)
    blocking_reason = models.JSONField(default=None, null=True, blank=True)
    field_reports = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.title


class Sku(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    product = models.ForeignKey(Product, related_name='skus', on_delete=models.CASCADE)
    name = models.CharField(max_length=255)
    price = models.BigIntegerField()
    cost_price = models.BigIntegerField(default=0)
    active_quantity = models.IntegerField(default=0)
    reserved_quantity = models.IntegerField(default=0)
    images = models.JSONField(default=list, blank=True)
    characteristics = models.JSONField(default=list, blank=True)
    deleted = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']


class Invoice(models.Model):
    class Status(models.TextChoices):
        CREATED = 'CREATED', 'CREATED'
        ACCEPTED = 'ACCEPTED', 'ACCEPTED'
        REJECTED = 'REJECTED', 'REJECTED'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    seller_id = models.UUIDField(db_index=True)
    warehouse_id = models.UUIDField(db_index=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.CREATED, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    accepted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']


class InvoiceItem(models.Model):
    id = models.BigAutoField(primary_key=True)
    invoice = models.ForeignKey(Invoice, related_name='items', on_delete=models.CASCADE)
    sku = models.ForeignKey(Sku, related_name='invoice_items', on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField()

    class Meta:
        unique_together = ('invoice', 'sku')


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


class InventoryOperation(models.Model):
    class Kind(models.TextChoices):
        RESERVE = 'RESERVE', 'RESERVE'
        UNRESERVE = 'UNRESERVE', 'UNRESERVE'
        FULFILL = 'FULFILL', 'FULFILL'

    key = models.CharField(max_length=128, primary_key=True)
    kind = models.CharField(max_length=16, choices=Kind.choices)
    payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)


class SellerProfile(models.Model):
    seller_id = models.UUIDField(unique=True, db_index=True)
    company_name = models.CharField(max_length=255, default=default_profile_company)
    contact_person = models.CharField(max_length=255, default=default_profile_contact)
    email = models.EmailField(blank=True, default='')
    phone = models.CharField(max_length=64, blank=True, default='')
    warehouse_id = models.UUIDField(default=uuid.uuid4)
    rating = models.DecimalField(max_digits=3, decimal_places=1, default=4.9)
    reviews = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['seller_id']
