from uuid import UUID, uuid4, uuid5, NAMESPACE_URL

from django.utils.text import slugify
from rest_framework import serializers

from .models import Category, Invoice, InvoiceItem, Product, SellerProfile, Sku


def _normalize_product_images(images):
    normalized = []
    for index, image in enumerate(images or []):
        if not isinstance(image, dict):
            continue
        image_id = image.get('id')
        if image_id:
            normalized_id = str(image_id)
        else:
            url = image.get('url', '')
            normalized_id = str(uuid5(NAMESPACE_URL, url)) if url else str(uuid4())
        normalized.append({
            'id': normalized_id,
            'url': image.get('url', ''),
            'ordering': image.get('ordering', index),
        })
    return normalized


def _product_slug(product):
    api_slug = getattr(product, '_api_slug', None)
    if api_slug:
        return api_slug
    return slugify(product.title) or str(product.id)


def _blocking_reason_id(product):
    reason = product.blocking_reason
    if not isinstance(reason, dict):
        return None
    raw = reason.get('id') or reason.get('blocking_reason_id')
    if not raw:
        return None
    try:
        return str(UUID(str(raw)))
    except (TypeError, ValueError):
        return None


def _moderator_comment(product):
    reports = product.field_reports or []
    messages = [report.get('message') for report in reports if isinstance(report, dict) and report.get('message')]
    if messages:
        return '; '.join(str(message) for message in messages)
    reason = product.blocking_reason
    if isinstance(reason, dict):
        return reason.get('comment') or reason.get('message') or reason.get('title')
    return None


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ['id', 'name']


class SkuSerializer(serializers.ModelSerializer):
    product_id = serializers.UUIDField(source='product.id', read_only=True)
    product_title = serializers.CharField(source='product.title', read_only=True)

    class Meta:
        model = Sku
        fields = [
            'id',
            'product_id',
            'product_title',
            'name',
            'price',
            'cost_price',
            'active_quantity',
            'reserved_quantity',
            'images',
            'characteristics',
            'deleted',
        ]


class CatalogSkuSerializer(serializers.ModelSerializer):
    class Meta:
        model = Sku
        fields = ['id', 'name', 'price', 'active_quantity', 'images', 'characteristics']


class ProductSerializer(serializers.ModelSerializer):
    seller_id = serializers.UUIDField(read_only=True)
    category_id = serializers.UUIDField(source='category_id', read_only=True)
    slug = serializers.SerializerMethodField()
    blocking_reason_id = serializers.SerializerMethodField()
    moderator_comment = serializers.SerializerMethodField()
    skus = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            'id',
            'seller_id',
            'category_id',
            'title',
            'slug',
            'description',
            'status',
            'deleted',
            'blocking_reason_id',
            'moderator_comment',
            'images',
            'characteristics',
            'skus',
            'created_at',
            'updated_at',
        ]

    def get_slug(self, obj):
        return _product_slug(obj)

    def get_blocking_reason_id(self, obj):
        return _blocking_reason_id(obj)

    def get_moderator_comment(self, obj):
        return _moderator_comment(obj)

    def get_skus(self, obj):
        skus = [sku for sku in obj.skus.all() if not getattr(sku, 'deleted', False)]
        return SkuSerializer(skus, many=True).data

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data['images'] = _normalize_product_images(instance.images)
        return data


class CatalogProductSerializer(serializers.ModelSerializer):
    category = CategorySerializer(read_only=True)
    skus = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            'id',
            'title',
            'description',
            'status',
            'category',
            'images',
            'characteristics',
            'skus',
            'created_at',
            'updated_at',
        ]

    def get_skus(self, obj):
        skus = [sku for sku in obj.skus.all() if not getattr(sku, 'deleted', False) and int(sku.active_quantity or 0) > 0]
        return CatalogSkuSerializer(skus, many=True).data


class CreateProductRequestSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=255)
    description = serializers.CharField(max_length=5000, allow_blank=False)
    category_id = serializers.UUIDField()
    slug = serializers.CharField(max_length=255, required=False, allow_null=True, allow_blank=True)
    images = serializers.ListField(child=serializers.DictField(), required=True)
    characteristics = serializers.ListField(child=serializers.DictField(), required=False)

    def validate(self, attrs):
        if not Category.objects.filter(id=attrs['category_id']).exists():
            raise serializers.ValidationError({'category_id': 'Unknown category_id.'})
        if not attrs.get('images'):
            raise serializers.ValidationError({'images': 'At least one image is required.'})
        return attrs


class UpdateProductRequestSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=255, required=False)
    description = serializers.CharField(required=False, allow_blank=True)
    category_id = serializers.UUIDField(required=False)
    images = serializers.ListField(child=serializers.DictField(), required=False)
    characteristics = serializers.ListField(child=serializers.DictField(), required=False)

    def validate(self, attrs):
        if attrs.get('category_id') and not Category.objects.filter(id=attrs['category_id']).exists():
            raise serializers.ValidationError({'category_id': 'Unknown category_id.'})
        return attrs


class CreateSkuRequestSerializer(serializers.Serializer):
    product_id = serializers.UUIDField()
    name = serializers.CharField(max_length=255)
    price = serializers.IntegerField(min_value=0)
    cost_price = serializers.IntegerField(min_value=0, required=False, default=0)
    active_quantity = serializers.IntegerField(min_value=0)
    images = serializers.ListField(child=serializers.DictField(), required=True)
    characteristics = serializers.ListField(child=serializers.DictField(), required=False)

    def validate(self, attrs):
        if not attrs.get('images'):
            raise serializers.ValidationError({'images': 'At least one image is required.'})
        return attrs


class UpdateSkuRequestSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField(max_length=255, required=False)
    price = serializers.IntegerField(min_value=0, required=False)
    cost_price = serializers.IntegerField(min_value=0, required=False)
    active_quantity = serializers.IntegerField(min_value=0, required=False)
    images = serializers.ListField(child=serializers.DictField(), required=False)
    characteristics = serializers.ListField(child=serializers.DictField(), required=False)


class InvoiceItemRequestSerializer(serializers.Serializer):
    sku_id = serializers.UUIDField()
    quantity = serializers.IntegerField(min_value=1)


class CreateInvoiceRequestSerializer(serializers.Serializer):
    seller_id = serializers.UUIDField(required=False)
    warehouse_id = serializers.UUIDField()
    items = InvoiceItemRequestSerializer(many=True, allow_empty=False)


class AcceptInvoiceRequestSerializer(serializers.Serializer):
    invoice_id = serializers.UUIDField()


class InventoryRequestItemSerializer(serializers.Serializer):
    sku_id = serializers.UUIDField()
    quantity = serializers.IntegerField(min_value=1)


class ReserveRequestSerializer(serializers.Serializer):
    idempotency_key = serializers.CharField(max_length=128)
    items = InventoryRequestItemSerializer(many=True, allow_empty=False)


class FulfillRequestSerializer(serializers.Serializer):
    order_id = serializers.CharField(max_length=128)
    items = InventoryRequestItemSerializer(many=True, allow_empty=False)


class ModerationDecisionSerializer(serializers.Serializer):
    idempotency_key = serializers.CharField(max_length=128)
    product_id = serializers.UUIDField()
    status = serializers.ChoiceField(choices=[Product.Status.MODERATED, Product.Status.BLOCKED])
    hard_block = serializers.BooleanField(required=False, default=False)
    blocking_reason = serializers.JSONField(required=False, allow_null=True)
    field_reports = serializers.ListField(child=serializers.DictField(), required=False)


class InvoiceItemSerializer(serializers.ModelSerializer):
    sku_id = serializers.UUIDField(source='sku.id', read_only=True)

    class Meta:
        model = InvoiceItem
        fields = ['sku_id', 'quantity']


class InvoiceSerializer(serializers.ModelSerializer):
    items = InvoiceItemSerializer(many=True, read_only=True)

    class Meta:
        model = Invoice
        fields = ['id', 'seller_id', 'warehouse_id', 'status', 'items', 'created_at', 'accepted_at']


class DashboardMetricSerializer(serializers.Serializer):
    label = serializers.CharField()
    value = serializers.IntegerField(min_value=0)


class DashboardOverviewSerializer(serializers.Serializer):
    total_products = serializers.IntegerField(min_value=0)
    total_skus = serializers.IntegerField(min_value=0)
    total_stock = serializers.IntegerField(min_value=0)
    created_products = serializers.IntegerField(min_value=0)
    on_moderation_products = serializers.IntegerField(min_value=0)
    blocked_products = serializers.IntegerField(min_value=0)
    pending_invoices = serializers.IntegerField(min_value=0)
    accepted_invoices = serializers.IntegerField(min_value=0)


class DashboardStatsSerializer(serializers.Serializer):
    product_statuses = DashboardMetricSerializer(many=True)
    low_stock_skus = SkuSerializer(many=True)
    recent_products = ProductSerializer(many=True)
    recent_invoices = InvoiceSerializer(many=True)


class SellerProfileSerializer(serializers.ModelSerializer):
    since = serializers.SerializerMethodField()

    class Meta:
        model = SellerProfile
        fields = [
            'seller_id',
            'company_name',
            'contact_person',
            'email',
            'phone',
            'warehouse_id',
            'rating',
            'reviews',
            'since',
            'created_at',
            'updated_at',
        ]

    def get_since(self, obj):
        return str(obj.created_at.year)


class SellerProfileUpdateSerializer(serializers.Serializer):
    company_name = serializers.CharField(max_length=255, required=False)
    contact_person = serializers.CharField(max_length=255, required=False)
    email = serializers.EmailField(required=False, allow_blank=True)
    phone = serializers.CharField(max_length=64, required=False, allow_blank=True)
    warehouse_id = serializers.UUIDField(required=False)
