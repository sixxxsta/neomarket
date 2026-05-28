from django.utils.text import slugify
from rest_framework import serializers

from .models import Category, Product, Sku, ProductImage, ProductAttribute


def _serialize_image(image) -> dict | None:
    if not image:
        return None
    return {
        "url": image.image_url,
        "ordering": image.order,
    }


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ["id", "name", "slug"]


class CategoryTreeItemSerializer(serializers.ModelSerializer):
    parent_id = serializers.SerializerMethodField()
    children = serializers.SerializerMethodField()

    class Meta:
        model = Category
        fields = ["id", "name", "parent_id", "children"]

    def get_parent_id(self, obj):
        return obj.parent_id

    def get_children(self, obj) -> list[dict]:
        children = obj.children.order_by("name")
        return CategoryTreeItemSerializer(children, many=True).data


class CategoryParentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ["id", "name", "slug"]


class CategoryDetailSerializer(serializers.ModelSerializer):
    parent = CategoryParentSerializer(read_only=True)
    product_count = serializers.SerializerMethodField()
    seo = serializers.SerializerMethodField()
    meta_tags = serializers.SerializerMethodField()

    class Meta:
        model = Category
        fields = [
            "id",
            "name",
            "slug",
            "description",
            "parent",
            "product_count",
            "seo",
            "meta_tags",
            "image_url",
            "is_active",
            "created_at",
            "updated_at",
        ]

    def get_product_count(self, obj) -> int | None:
        include_count = self.context.get("include_product_count", False)
        if not include_count:
            return None
        return obj.products.filter(status=Product.Status.MODERATED).count()

    def get_seo(self, obj) -> dict:
        return {
            "title": f"Купить {obj.name.lower()} в интернет-магазине | NeoMarket",
            "description": obj.description or f"{obj.name} по выгодным ценам в NeoMarket.",
            "keywords": [obj.name.lower()],
        }

    def get_meta_tags(self, obj) -> dict:
        return {
            "og_title": f"{obj.name} | NeoMarket",
            "og_description": obj.description or f"Купить {obj.name.lower()} в интернет-магазине.",
        }


class SkuShortSerializer(serializers.ModelSerializer):
    image = serializers.SerializerMethodField()

    class Meta:
        model = Sku
        fields = ["id", "name", "price", "image"]

    def get_image(self, obj) -> dict | None:
        main_image = obj.product.images.filter(is_main=True).first()
        if main_image:
            return _serialize_image(main_image)
        return _serialize_image(obj.product.images.first())


class SkuDetailSerializer(serializers.ModelSerializer):
    available_quantity = serializers.IntegerField(source="active_quantity", read_only=True)
    sku_code = serializers.SerializerMethodField()
    attributes = serializers.SerializerMethodField()
    characteristics = serializers.SerializerMethodField()
    images = serializers.SerializerMethodField()
    discount = serializers.SerializerMethodField()
    image = serializers.SerializerMethodField()

    class Meta:
        model = Sku
        fields = [
            "id",
            "name",
            "sku_code",
            "price",
            "old_price",
            "available_quantity",
            "active_quantity",
            "attributes",
            "characteristics",
            "images",
            "discount",
            "image",
        ]

    def get_sku_code(self, obj) -> str:
        return str(obj.id).split("-")[0]

    def get_attributes(self, obj) -> dict:
        return {str(name): str(value) for name, value in (obj.attributes or {}).items()}

    def get_characteristics(self, obj) -> list[dict]:
        return [
            {"name": str(name), "value": str(value)}
            for name, value in (obj.attributes or {}).items()
        ]

    def get_images(self, obj) -> list[dict]:
        return [_serialize_image(image) for image in obj.product.images.all()]

    old_price = serializers.SerializerMethodField()

    def get_old_price(self, _obj):
        return None

    def get_discount(self, _obj) -> int:
        return 0

    def get_image(self, obj) -> str | None:
        main_image = obj.product.images.filter(is_main=True).first()
        if main_image:
            return main_image.image_url
        first_image = obj.product.images.first()
        return first_image.image_url if first_image else None


class ProductImageSerializer(serializers.ModelSerializer):
    url = serializers.CharField(source="image_url", read_only=True)
    ordering = serializers.IntegerField(source="order", read_only=True)

    class Meta:
        model = ProductImage
        fields = ["url", "ordering"]


class ProductAttributeSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductAttribute
        fields = ["id", "name", "value"]


class ProductShortSerializer(serializers.ModelSerializer):
    name = serializers.CharField(source="title", read_only=True)
    slug = serializers.SerializerMethodField()
    min_price = serializers.SerializerMethodField()
    old_price = serializers.SerializerMethodField()
    has_stock = serializers.SerializerMethodField()
    images = serializers.SerializerMethodField()
    category = serializers.SerializerMethodField()
    # Legacy aliases
    image = serializers.SerializerMethodField()
    price = serializers.SerializerMethodField()
    in_stock = serializers.SerializerMethodField()
    is_in_cart = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            "id",
            "name",
            "slug",
            "title",
            "min_price",
            "old_price",
            "has_stock",
            "images",
            "category",
            "image",
            "price",
            "in_stock",
            "is_in_cart",
        ]

    def get_slug(self, obj) -> str:
        return slugify(obj.title, allow_unicode=True)

    def _main_image(self, obj):
        main_image = obj.images.filter(is_main=True).first()
        return main_image or obj.images.first()

    def get_images(self, obj) -> list[dict]:
        result = []
        for image in obj.images.all().order_by("order", "id"):
            result.append(
                {
                    "id": str(image.id),
                    "url": image.image_url,
                    "ordering": image.order,
                    "is_main": image.is_main,
                }
            )
        return result

    def get_category(self, obj):
        if not obj.category:
            return None
        path = []
        current = obj.category
        seen = set()
        while current and current.id not in seen:
            seen.add(current.id)
            path.append(current.name)
            current = current.parent
        path.reverse()
        return {
            "id": str(obj.category.id),
            "name": obj.category.name,
            "parent_id": str(obj.category.parent_id) if obj.category.parent_id else None,
            "level": max(len(path) - 1, 0),
            "path": path,
        }

    def get_image(self, obj) -> str | None:
        image = self._main_image(obj)
        return image.image_url if image else None

    def get_min_price(self, obj) -> int:
        sku = obj.skus.order_by("price").first()
        return int(sku.price) if sku else 0

    def get_old_price(self, _obj):
        return None

    def get_price(self, obj) -> int:
        return self.get_min_price(obj)

    def get_has_stock(self, obj) -> bool:
        return obj.skus.filter(active_quantity__gt=0).exists()

    def get_in_stock(self, obj) -> bool:
        return self.get_has_stock(obj)

    def get_is_in_cart(self, _obj) -> bool:
        return False


class ProductShortListResponseSerializer(serializers.Serializer):
    total_count = serializers.IntegerField()
    limit = serializers.IntegerField()
    offset = serializers.IntegerField()
    items = ProductShortSerializer(many=True)


class ProductDetailSerializer(serializers.ModelSerializer):
    category = CategorySerializer(read_only=True)
    characteristics = serializers.SerializerMethodField()
    images = ProductImageSerializer(many=True, read_only=True)
    skus = SkuDetailSerializer(many=True, read_only=True)
    slug = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            "id",
            "slug",
            "title",
            "description",
            "status",
            "category",
            "images",
            "characteristics",
            "skus",
        ]

    def get_characteristics(self, obj) -> list[dict]:
        attributes = obj.attributes.all()
        return ProductAttributeSerializer(attributes, many=True).data

    def get_slug(self, obj) -> str:
        return slugify(obj.title, allow_unicode=True)


class FilterItemSerializer(serializers.Serializer):
    slug = serializers.CharField()
    name = serializers.CharField()
    type = serializers.ChoiceField(choices=["list", "range", "switch"])
    value = serializers.ListField(required=False)
    min = serializers.IntegerField(required=False)
    max = serializers.IntegerField(required=False)


class FacetValueSerializer(serializers.Serializer):
    value = serializers.CharField()
    count = serializers.IntegerField(min_value=0)


class FacetSerializer(serializers.Serializer):
    name = serializers.CharField()
    values = FacetValueSerializer(many=True)
