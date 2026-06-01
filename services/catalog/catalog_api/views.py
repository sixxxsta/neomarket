import random
import re
import json
from datetime import datetime
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from uuid import UUID

from django.conf import settings
from django.db.models import Q
from django.utils.text import slugify
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Category, Product, Sku
from .serializers import (
    CategoryDetailSerializer,
    FacetSerializer,
    CategoryTreeItemSerializer,
    FilterItemSerializer,
    ProductDetailSerializer,
    ProductShortSerializer,
    SkuDetailSerializer,
    SkuShortSerializer,
)

ALLOWED_SORTS = {"rating", "popularity", "price_asc", "price_desc", "date_desc", "discount_desc"}
FILTER_RE = re.compile(r"^filters\[(?P<slug>[^\]]+)\](?:\[(?P<nested>[^\]]+)\])?$")


def _parse_int(value, default, minimum=None, maximum=None):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default

    if minimum is not None:
        parsed = max(minimum, parsed)
    if maximum is not None:
        parsed = min(maximum, parsed)
    return parsed


def _is_uuid(value):
    try:
        UUID(str(value))
        return True
    except (TypeError, ValueError):
        return False


def _error(code, message, http_status):
    return Response({"code": code, "message": message}, status=http_status)


def _normalize_filter_slug(value):
    normalized = slugify(str(value), allow_unicode=True).replace("-", "_")
    return normalized or str(value).strip().lower().replace(" ", "_")


def _parse_filters(query_params):
    filters = {}
    for key, value in query_params.items():
        match = FILTER_RE.match(key)
        if not match:
            continue
        slug = match.group("slug")
        nested = match.group("nested")
        if nested:
            filters.setdefault(slug, {})[nested] = value
        else:
            filters[slug] = value
    return filters


def _catalog_b2b_query(request, limit, offset):
    query = dict(request.query_params)
    query["limit"] = limit
    query["offset"] = offset
    query["status"] = Product.Status.MODERATED
    query["deleted"] = "false"
    query["active_quantity__gt"] = 0
    return query


def _b2b_request_url(path: str, query: dict) -> str:
    base = str(getattr(settings, "B2B_PRODUCTS_URL", "")).rstrip("/")
    if not base:
        return ""
    url = f"{base}{path}"
    encoded = urllib.parse.urlencode(query, doseq=True)
    return f"{url}?{encoded}" if encoded else url


def _fetch_b2b_json(path: str, query: dict):
    url = _b2b_request_url(path, query=query)
    if not url:
        return None, _error("B2B_UNAVAILABLE", "B2B_PRODUCTS_URL is not configured", status.HTTP_502_BAD_GATEWAY)

    try:
        request = urllib.request.Request(
            url,
            headers={"X-Service-Key": getattr(settings, "INTERNAL_SERVICE_KEY", "neomarket-internal-key")},
        )
        with urllib.request.urlopen(request, timeout=float(getattr(settings, "B2B_TIMEOUT", 5.0))) as response:
            payload = json.loads(response.read().decode())
        return payload, None
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, json.JSONDecodeError):
        return None, _error("B2B_UNAVAILABLE", "B2B service is unavailable", status.HTTP_502_BAD_GATEWAY)


def _fetch_b2b_all_products(query: dict):
    items = []
    offset = int(query.get("offset") or 0)
    limit = int(query.get("limit") or 100)
    total_count = None

    while total_count is None or offset < total_count:
        page_query = dict(query)
        page_query["offset"] = offset
        page_query["limit"] = limit
        payload, error = _fetch_b2b_json("", page_query)
        if error:
            return None, error

        chunk = payload.get("items", []) or []
        items.extend(chunk)
        total_count = int(payload.get("total_count") or len(items))
        if not chunk:
            break
        offset += limit

    return items, None


def _is_truthy(value):
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _item_category_id(item):
    if not isinstance(item, dict):
        return None
    category = item.get("category") or {}
    if isinstance(category, dict) and category.get("id"):
        return str(category.get("id"))
    if item.get("category_id"):
        return str(item.get("category_id"))
    return None


def _item_status(item):
    if not isinstance(item, dict):
        return None
    return str(item.get("status") or "")


def _item_is_deleted(item):
    if not isinstance(item, dict):
        return True
    return _is_truthy(item.get("deleted", False))


def _item_active_quantity(item):
    if not isinstance(item, dict):
        return 0
    if item.get("active_quantity") is not None:
        try:
            return int(item.get("active_quantity") or 0)
        except (TypeError, ValueError):
            return 0

    total = 0
    for sku in item.get("skus") or []:
        if not isinstance(sku, dict):
            continue
        try:
            total += int(sku.get("active_quantity") or 0)
        except (TypeError, ValueError):
            continue
    return total


def _item_price_bounds(item):
    prices = []
    if not isinstance(item, dict):
        return 0, 0

    for key in ("min_price", "price"):
        if item.get(key) is not None:
            try:
                prices.append(int(item.get(key) or 0))
            except (TypeError, ValueError):
                continue

    for sku in item.get("skus") or []:
        if not isinstance(sku, dict):
            continue
        try:
            prices.append(int(sku.get("price") or 0))
        except (TypeError, ValueError):
            continue

    if not prices:
        return 0, 0
    return min(prices), max(prices)


def _item_timestamp(item):
    if not isinstance(item, dict):
        return 0.0
    raw_value = item.get("created_at") or item.get("updated_at")
    if isinstance(raw_value, datetime):
        return raw_value.timestamp()
    if not raw_value:
        return 0.0
    try:
        parsed = datetime.fromisoformat(str(raw_value).replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    return parsed.timestamp()


def _item_attribute_map(item):
    values = defaultdict(set)
    if not isinstance(item, dict):
        return values

    for characteristic in item.get("characteristics") or []:
        if not isinstance(characteristic, dict):
            continue
        slug = _normalize_filter_slug(characteristic.get("name") or "")
        value = str(characteristic.get("value") or "").strip()
        if slug and value:
            values[slug].add(value)

    for sku in item.get("skus") or []:
        if not isinstance(sku, dict):
            continue
        for characteristic in sku.get("characteristics") or []:
            if not isinstance(characteristic, dict):
                continue
            slug = _normalize_filter_slug(characteristic.get("name") or "")
            value = str(characteristic.get("value") or "").strip()
            if slug and value:
                values[slug].add(value)

    return values


def _item_matches_identity_filters(item, ids=None, sku_ids=None):
    if ids and str(item.get("id") or "") not in ids:
        return False
    if sku_ids:
        item_sku_ids = {str(sku.get("id") or "") for sku in item.get("skus") or [] if isinstance(sku, dict)}
        if not item_sku_ids.intersection(sku_ids):
            return False
    return True


def _item_matches_filters(item, category_id=None, filters=None, search=None):
    if _item_status(item) != Product.Status.MODERATED:
        return False
    if _item_is_deleted(item):
        return False
    if _item_active_quantity(item) <= 0:
        return False

    if category_id and _item_category_id(item) != str(category_id):
        return False

    if search:
        haystack = " ".join(
            [
                str(item.get("title") or ""),
                str(item.get("name") or ""),
                str(item.get("description") or ""),
            ]
        ).lower()
        if str(search).lower() not in haystack:
            return False

    attributes = _item_attribute_map(item)
    filters = filters or {}
    for slug, raw_value in filters.items():
        if slug == "availability":
            if _is_truthy(raw_value) and _item_active_quantity(item) <= 0:
                return False
            continue

        if slug == "price" and isinstance(raw_value, dict):
            min_price = _parse_int(raw_value.get("min"), default=None)
            max_price = _parse_int(raw_value.get("max"), default=None)
            item_min, item_max = _item_price_bounds(item)
            if min_price is not None and item_max < min_price:
                return False
            if max_price is not None and item_min > max_price:
                return False
            continue

        requested_values = raw_value if isinstance(raw_value, list) else str(raw_value).split(",")
        requested_values = [str(value).strip() for value in requested_values if str(value).strip()]
        if not requested_values:
            continue

        if not attributes.get(slug, set()).intersection(requested_values):
            return False

    return True


def _sort_catalog_items(items, sort):
    if sort == "price_asc":
        return sorted(items, key=lambda item: (_item_price_bounds(item)[0], _item_timestamp(item), str(item.get("id") or "")))
    if sort == "price_desc":
        return sorted(items, key=lambda item: (_item_price_bounds(item)[1], _item_timestamp(item), str(item.get("id") or "")), reverse=True)
    if sort == "discount_desc":
        return sorted(items, key=lambda item: (int(item.get("discount") or 0), _item_timestamp(item), str(item.get("id") or "")), reverse=True)
    if sort == "rating":
        return sorted(items, key=lambda item: (float(item.get("rating") or 0), _item_timestamp(item), str(item.get("id") or "")), reverse=True)
    if sort == "popularity":
        return sorted(items, key=lambda item: (int(item.get("popularity") or 0), _item_timestamp(item), str(item.get("id") or "")), reverse=True)
    return sorted(items, key=lambda item: (_item_timestamp(item), str(item.get("id") or "")), reverse=True)


def _catalog_filtered_sorted_items(request):
    limit = _parse_int(request.query_params.get("limit", 20), default=20, minimum=1, maximum=100)
    offset = _parse_int(request.query_params.get("offset", 0), default=0, minimum=0)
    sort = request.query_params.get("sort", "rating")
    if sort not in ALLOWED_SORTS:
        allowed = ", ".join(sorted(ALLOWED_SORTS))
        return None, _error("INVALID_REQUEST", f"Invalid sort parameter. Allowed: {allowed}", status.HTTP_400_BAD_REQUEST)

    category_id = request.query_params.get("category_id")
    if category_id and not _is_uuid(category_id):
        return None, _error("INVALID_REQUEST", "Invalid category_id", status.HTTP_400_BAD_REQUEST)

    search = request.query_params.get("search")
    if search is not None:
        search = str(search).strip()
        if search and len(search) < 3:
            return None, _error("INVALID_REQUEST", "Search query must be at least 3 characters", status.HTTP_400_BAD_REQUEST)

    ids_param = request.query_params.get("ids")
    sku_ids_param = request.query_params.get("sku_ids")
    ids = None
    sku_ids = None
    if ids_param:
        ids = [item.strip() for item in str(ids_param).split(",") if item.strip()]
        if any(not _is_uuid(item) for item in ids):
            return None, _error("INVALID_REQUEST", "Invalid ids filter", status.HTTP_400_BAD_REQUEST)
    if sku_ids_param:
        sku_ids = [item.strip() for item in str(sku_ids_param).split(",") if item.strip()]
        if any(not _is_uuid(item) for item in sku_ids):
            return None, _error("INVALID_REQUEST", "Invalid sku_ids filter", status.HTTP_400_BAD_REQUEST)

    query = _catalog_b2b_query(request, limit=200, offset=0)
    items, error = _fetch_b2b_all_products(query)
    if error:
        return None, error

    filters = _parse_filters(request.query_params)
    filtered_items = [
        item
        for item in items
        if _item_matches_identity_filters(item, ids=ids, sku_ids=sku_ids)
        and _item_matches_filters(item, category_id=category_id, filters=filters, search=search)
    ]
    sorted_items = _sort_catalog_items(filtered_items, sort)

    return {
        "items": sorted_items,
        "total_count": len(sorted_items),
        "limit": limit,
        "offset": offset,
    }, None


def _catalog_response_items(request):
    payload, error = _catalog_filtered_sorted_items(request)
    if error:
        return None, error

    limit = payload["limit"]
    offset = payload["offset"]
    items = payload["items"]
    total_count = payload["total_count"]

    return {
        "items": items[offset : offset + limit],
        "total_count": total_count,
        "total": total_count,
        "limit": limit,
        "offset": offset,
    }, None


def _catalog_filtered_items(request):
    payload, error = _catalog_filtered_sorted_items(request)
    if error:
        return None, error
    return payload["items"], None


def _base_products_queryset():
    return (
        Product.objects.filter(status=Product.Status.MODERATED)
        .select_related("category", "category__parent")
        .prefetch_related("skus", "images", "attributes")
    )


def _category_or_404(category_id):
    try:
        return Category.objects.select_related("parent").get(id=category_id, is_active=True), None
    except Category.DoesNotExist:
        return None, _error("NOT_FOUND", "Category not found", status.HTTP_404_NOT_FOUND)


def _build_attribute_map(product):
    values = defaultdict(set)
    labels = {}
    for attribute in product.attributes.all():
        slug = _normalize_filter_slug(attribute.name)
        labels.setdefault(slug, str(attribute.name))
        values[slug].add(str(attribute.value))

    for sku in product.skus.all():
        for name, value in (sku.attributes or {}).items():
            slug = _normalize_filter_slug(name)
            labels.setdefault(slug, str(name))
            values[slug].add(str(value))

    return values, labels


def _product_matches_filters(product, filters):
    attribute_values, _labels = _build_attribute_map(product)
    sku_prices = [int(sku.price) for sku in product.skus.all()]
    max_stock = max((int(sku.active_quantity or 0) for sku in product.skus.all()), default=0)

    for slug, raw_value in filters.items():
        if slug == "availability":
            if str(raw_value).lower() in {"1", "true", "yes", "on"} and max_stock <= 0:
                return False
            continue

        if slug == "price" and isinstance(raw_value, dict):
            min_price = _parse_int(raw_value.get("min"), default=None)
            max_price = _parse_int(raw_value.get("max"), default=None)
            if min_price is not None and sku_prices and max(sku_prices) < min_price:
                return False
            if max_price is not None and sku_prices and min(sku_prices) > max_price:
                return False
            continue

        requested_values = raw_value if isinstance(raw_value, list) else str(raw_value).split(",")
        requested_values = [str(item).strip() for item in requested_values if str(item).strip()]
        if not requested_values:
            continue
        available_values = {str(item) for item in attribute_values.get(slug, set())}
        if not available_values.intersection(requested_values):
            return False

    return True


def _sorted_products(products, sort):
    if sort == "price_asc":
        return sorted(products, key=lambda item: (min((sku.price for sku in item.skus.all()), default=0), -item.created_at.timestamp()))
    if sort == "price_desc":
        return sorted(products, key=lambda item: (max((sku.price for sku in item.skus.all()), default=0), item.created_at.timestamp()), reverse=True)
    if sort in {"rating", "popularity", "discount_desc", "date_desc"}:
        return sorted(products, key=lambda item: item.created_at, reverse=True)
    return sorted(products, key=lambda item: item.created_at, reverse=True)


def _category_chain(category):
    chain = []
    seen = set()
    current = category
    while current:
        if current.id in seen:
            return None, _error("orphan_node", "category hierarchy is broken", status.HTTP_422_UNPROCESSABLE_ENTITY)
        seen.add(current.id)
        chain.append(current)
        current = current.parent
    chain.reverse()
    return chain, None


def _category_url(chain, current):
    slug_path = "/".join(item.slug for item in chain[: current + 1])
    return f"/catalog/{slug_path}"


@extend_schema_view(
    get=extend_schema(operation_id="catalog_list_products", responses=OpenApiTypes.OBJECT),
)
class ProductListView(APIView):
    def get(self, request):
        payload, error = _catalog_response_items(request)
        if error:
            return error
        return Response(payload)


@extend_schema_view(
    get=extend_schema(operation_id="catalog_get_product", responses=ProductDetailSerializer),
)
class ProductDetailView(APIView):
    def get(self, request, id):
        try:
            product = _base_products_queryset().get(
                id=id,
                status=Product.Status.MODERATED,
            )
        except Product.DoesNotExist:
            return _error("NOT_FOUND", "Product not found", status.HTTP_404_NOT_FOUND)

        serializer = ProductDetailSerializer(product)
        return Response(serializer.data)


@extend_schema_view(
    get=extend_schema(operation_id="catalog_list_similar_products", responses=OpenApiTypes.OBJECT),
)
class ProductSimilarView(APIView):
    def get(self, request, id):
        limit = _parse_int(request.query_params.get("limit", 8), default=8, minimum=1, maximum=20)
        offset = _parse_int(request.query_params.get("offset", 0), default=0, minimum=0)

        try:
            product = _base_products_queryset().get(id=id, status=Product.Status.MODERATED)
        except Product.DoesNotExist:
            return _error("NOT_FOUND", "Product not found", status.HTTP_404_NOT_FOUND)

        category_id = request.query_params.get("category") or str(product.category_id)
        if not _is_uuid(category_id):
            return _error("INVALID_REQUEST", "Nonexistent category id", status.HTTP_400_BAD_REQUEST)

        category_products = [item for item in _base_products_queryset().filter(category_id=category_id) if item.id != product.id]
        if len(category_products) < limit and product.category.parent_id:
            parent_products = [
                item
                for item in _base_products_queryset().filter(category=product.category.parent)
                if item.id != product.id and item.id not in {candidate.id for candidate in category_products}
            ]
            category_products.extend(parent_products)

        randomizer = random.Random(f"{product.id}:{category_id}")
        randomizer.shuffle(category_products)
        total_count = len(category_products)
        items = category_products[offset : offset + limit]
        serializer = ProductShortSerializer(items, many=True)

        return Response(
            {
                "items": serializer.data,
                "total_count": total_count,
                "limit": limit,
                "offset": offset,
            }
        )


@extend_schema_view(
    get=extend_schema(operation_id="catalog_list_product_skus", responses=SkuShortSerializer(many=True)),
)
class ProductSkuListView(APIView):
    def get(self, request, product_id):
        skus = Sku.objects.filter(product_id=product_id, product__status=Product.Status.MODERATED).select_related("product")
        serializer = SkuShortSerializer(skus, many=True)
        return Response(serializer.data)


@extend_schema_view(
    get=extend_schema(operation_id="catalog_get_product_sku", responses=SkuDetailSerializer),
)
class ProductSkuDetailView(APIView):
    def get(self, request, product_id, sku_id):
        try:
            sku = Sku.objects.select_related("product").get(id=sku_id, product_id=product_id, product__status=Product.Status.MODERATED)
        except Sku.DoesNotExist:
            return _error("NOT_FOUND", "SKU not found", status.HTTP_404_NOT_FOUND)

        serializer = SkuDetailSerializer(sku)
        return Response(serializer.data)


@extend_schema_view(
    get=extend_schema(operation_id="catalog_get_categories_tree", responses=OpenApiTypes.OBJECT),
)
class CategoryTreeView(APIView):
    def get(self, request):
        roots = Category.objects.filter(parent__isnull=True, is_active=True).order_by("name")
        serializer = CategoryTreeItemSerializer(roots, many=True)
        return Response({"items": serializer.data})


@extend_schema_view(
    get=extend_schema(operation_id="catalog_get_category", responses=CategoryDetailSerializer),
)
class CategoryDetailView(APIView):
    def get(self, request, id):
        include_product_count = str(request.query_params.get("include_product_count", "false")).lower() == "true"
        category, error = _category_or_404(id)
        if error:
            return error

        serializer = CategoryDetailSerializer(category, context={"include_product_count": include_product_count})
        return Response(serializer.data)


@extend_schema_view(
    get=extend_schema(operation_id="catalog_get_category_filters", responses=OpenApiTypes.OBJECT),
)
class CategoryFiltersView(APIView):
    def get(self, request, id):
        category, error = _category_or_404(id)
        if error:
            return error

        products = list(_base_products_queryset().filter(category=category))
        skus = [sku for product in products for sku in product.skus.all()]
        min_price = min((int(sku.price) for sku in skus), default=0)
        max_price = max((int(sku.price) for sku in skus), default=0)

        dynamic_values = defaultdict(set)
        dynamic_labels = {}
        for product in products:
            attribute_values, labels = _build_attribute_map(product)
            for slug, values in attribute_values.items():
                dynamic_labels.setdefault(slug, labels.get(slug, slug))
                dynamic_values[slug].update(values)

        items = [
            {"slug": "availability", "name": "В наличии", "type": "switch"},
            {"slug": "price", "name": "Цена", "type": "range", "min": min_price, "max": max_price},
        ]
        for slug in sorted(dynamic_values):
            values = sorted(dynamic_values[slug])
            if values:
                items.append({"slug": slug, "name": dynamic_labels.get(slug, slug), "type": "list", "value": values})

        serializer = FilterItemSerializer(items, many=True)
        return Response({"items": serializer.data})


@extend_schema_view(
    get=extend_schema(operation_id="catalog_get_facets", responses=OpenApiTypes.OBJECT),
)
class CatalogFacetsView(APIView):
    def get(self, request):
        category_id = request.query_params.get("category_id")
        if not category_id or not _is_uuid(category_id):
            return _error("INVALID_REQUEST", "category_id must be provided", status.HTTP_400_BAD_REQUEST)

        items, error = _catalog_filtered_items(request)
        if error:
            return error

        counters = defaultdict(lambda: defaultdict(int))
        for product in items:
            for characteristic in product.get("characteristics") or []:
                if not isinstance(characteristic, dict):
                    continue
                slug = _normalize_filter_slug(characteristic.get("name") or "")
                value = str(characteristic.get("value") or "").strip()
                if slug and value:
                    counters[slug][value] += 1

            for sku in product.get("skus") or []:
                if not isinstance(sku, dict):
                    continue
                for characteristic in sku.get("characteristics") or []:
                    if not isinstance(characteristic, dict):
                        continue
                    slug = _normalize_filter_slug(characteristic.get("name") or "")
                    value = str(characteristic.get("value") or "").strip()
                    if slug and value:
                        counters[slug][value] += 1

        facets = [
            {"name": slug, "values": [{"value": value, "count": count} for value, count in sorted(values.items())]}
            for slug, values in sorted(counters.items())
        ]
        serializer = FacetSerializer(facets, many=True)
        return Response({"category_id": str(category_id), "facets": serializer.data})


@extend_schema_view(
    get=extend_schema(operation_id="catalog_get_breadcrumbs", responses=OpenApiTypes.OBJECT),
)
class BreadcrumbsView(APIView):
    def get(self, request):
        category_id = request.query_params.get("category_id")
        product_id = request.query_params.get("product_id")
        if category_id and product_id:
            return _error("ambiguous_param", "only one of category_id or product_id must be provided", status.HTTP_400_BAD_REQUEST)
        if not category_id and not product_id:
            return _error("missing_param", "category_id or product_id must be provided", status.HTTP_400_BAD_REQUEST)

        if category_id:
            if not _is_uuid(category_id):
                return _error("INVALID_REQUEST", "Invalid category_id", status.HTTP_400_BAD_REQUEST)
            category, error = _category_or_404(category_id)
            if error:
                return error
            chain, error = _category_chain(category)
            if error:
                return error
            data = [
                {
                    "id": str(item.id),
                    "slug": item.slug,
                    "name": item.name,
                    "url": _category_url(chain, index),
                    "level": index,
                    "is_current": index == len(chain) - 1,
                }
                for index, item in enumerate(chain)
            ]
            return Response({"data": data, "meta": {"resolved_via": "category_id", "category_id": str(category.id)}})

        if not _is_uuid(product_id):
            return _error("INVALID_REQUEST", "Invalid product_id", status.HTTP_400_BAD_REQUEST)
        try:
            product = _base_products_queryset().get(id=product_id, status=Product.Status.MODERATED)
        except Product.DoesNotExist:
            return _error("NOT_FOUND", "Product not found", status.HTTP_404_NOT_FOUND)
        chain, error = _category_chain(product.category)
        if error:
            return error
        data = [
            {
                "id": str(item.id),
                "slug": item.slug,
                "name": item.name,
                "url": _category_url(chain, index),
                "level": index,
                "is_current": False,
            }
            for index, item in enumerate(chain)
        ]
        data.append(
            {
                "id": str(product.id),
                "slug": slugify(product.title, allow_unicode=True),
                "name": product.title,
                "url": f"/products/{product.id}",
                "level": len(chain),
                "is_current": True,
            }
        )
        return Response({"data": data, "meta": {"resolved_via": "product_id", "product_id": str(product.id), "category_id": str(product.category_id)}})
