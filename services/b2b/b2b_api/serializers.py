from rest_framework import serializers

from .models import Category, Invoice, InvoiceItem, Product, SellerProfile, Sku


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
    category = CategorySerializer(read_only=True)
    skus = serializers.SerializerMethodField()
    skus_count = serializers.SerializerMethodField()
    total_active_quantity = serializers.SerializerMethodField()

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
            'skus_count',
            'total_active_quantity',
            'blocking_reason',
            'field_reports',
            'deleted',
            'created_at',
            'updated_at',
        ]

    def get_skus(self, obj):
        skus = [sku for sku in obj.skus.all() if not getattr(sku, 'deleted', False)]
        return SkuSerializer(skus, many=True).data

    def get_skus_count(self, obj):
        annotated = getattr(obj, 'skus_count', None)
        if annotated is not None:
            return int(annotated)
        return obj.skus.filter(deleted=False).count()

    def get_total_active_quantity(self, obj):
        annotated = getattr(obj, 'total_active_quantity', None)
        if annotated is not None:
            return int(annotated or 0)
        return sum(max(int(sku.active_quantity or 0), 0) for sku in obj.skus.filter(deleted=False))


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
    description = serializers.CharField(required=False, allow_blank=True)
    category_id = serializers.UUIDField(required=False)
    category_name = serializers.CharField(max_length=255, required=False, allow_blank=False)
    images = serializers.ListField(child=serializers.DictField(), required=True)
    characteristics = serializers.ListField(child=serializers.DictField(), required=False)

    def validate(self, attrs):
        if not attrs.get('category_id') and not attrs.get('category_name'):
            raise serializers.ValidationError('Either category_id or category_name is required.')
        if attrs.get('category_id') and not Category.objects.filter(id=attrs['category_id']).exists():
            raise serializers.ValidationError({'category_id': 'Unknown category_id.'})
        if not attrs.get('images'):
            raise serializers.ValidationError({'images': 'At least one image is required.'})
        return attrs


class UpdateProductRequestSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=255, required=False)
    description = serializers.CharField(required=False, allow_blank=True)
    category_id = serializers.UUIDField(required=False)
    category_name = serializers.CharField(max_length=255, required=False, allow_blank=False)
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
