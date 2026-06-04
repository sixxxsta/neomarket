from uuid import UUID

import jwt
import requests
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, extend_schema_view
from jwt import InvalidTokenError
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Banner, BannerEvent, Cart, CartItem, Collection, Favorite, ProductEventInbox, Subscription
from .serializers import AddCartItemRequestSerializer, BannerEventsRequestSerializer, CartItemSerializer, FavoriteMutationSerializer, SubscribeRequestSerializer, UpdateCartItemRequestSerializer


def _parse_uuid(value):
    if not value:
        return None
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


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


def _error(message, code, http_status):
    return Response({"code": code, "message": message}, status=http_status)


def _decode_jwt_payload(token):
    algorithm = settings.JWT_ALGORITHM
    key = settings.JWT_SECRET if algorithm.startswith("HS") else settings.JWT_PUBLIC_KEY
    if not key:
        return None

    decode_kwargs = {
        "algorithms": [algorithm],
        "options": {
            "verify_signature": True,
            "verify_exp": True,
            "verify_aud": bool(settings.JWT_AUDIENCE),
            "verify_iss": bool(settings.JWT_ISSUER),
        },
    }
    if settings.JWT_AUDIENCE:
        decode_kwargs["audience"] = settings.JWT_AUDIENCE
    if settings.JWT_ISSUER:
        decode_kwargs["issuer"] = settings.JWT_ISSUER

    try:
        return jwt.decode(token, key=key, **decode_kwargs)
    except InvalidTokenError:
        return None


def _extract_identity(request):
    session_id = _parse_uuid(request.headers.get("X-Session-Id"))
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth.split(" ", 1)[1].strip()
        payload = _decode_jwt_payload(token)
        if payload is None:
            return None, session_id, True
        user_candidate = payload.get("sub") or payload.get("user_id")
        user_id = _parse_uuid(user_candidate)
        if user_id:
            return user_id, session_id, False

        return None, session_id, True

    # Backward-compatible bootstrap mode.
    user_id = _parse_uuid(request.headers.get("X-User-Id"))
    return user_id, session_id, False


def _get_cart_identity(request):
    user_id, session_id, token_error = _extract_identity(request)
    if token_error:
        return None, None, _error("Невалидный JWT токен", "UNAUTHORIZED", status.HTTP_401_UNAUTHORIZED)
    if not user_id and not session_id:
        return None, None, _error("Передайте X-User-Id или X-Session-Id", "MISSING_CART_IDENTITY", status.HTTP_400_BAD_REQUEST)
    return user_id, session_id, None


def _get_user_id_for_favorites(request):
    """user_id only from JWT (or X-User-Id header); never from query/body — IDOR protection."""
    user_id, _session_id, token_error = _extract_identity(request)
    if token_error:
        return None, _error("Невалидный JWT токен", "UNAUTHORIZED", status.HTTP_401_UNAUTHORIZED)
    if not user_id:
        return None, _error("Требуется авторизация", "UNAUTHORIZED", status.HTTP_401_UNAUTHORIZED)
    return user_id, None


def _serialize_subscription_response(subscription, product):
    return {
        "id": str(subscription.id),
        "product": product,
        "notify_on": subscription.notify_on,
        "created_at": subscription.created_at,
    }


def _favorite_mutation_payload(favorite, message):
    return {
        "product_id": str(favorite.product_id),
        "user_id": str(favorite.user_id),
        "added_at": favorite.added_at,
        "message": message,
    }


def _catalog_request(params):
    try:
        response = requests.get(
            settings.CATALOG_PRODUCTS_URL,
            params=params,
            headers={"X-Service-Key": settings.INTERNAL_SERVICE_KEY},
            timeout=settings.CATALOG_TIMEOUT,
        )
    except requests.RequestException:
        return None, _error("Сервис товаров временно недоступен, попробуйте позже", "B2B_UNAVAILABLE", status.HTTP_503_SERVICE_UNAVAILABLE)

    if response.status_code != status.HTTP_200_OK:
        return None, _error("Сервис товаров временно недоступен, попробуйте позже", "B2B_UNAVAILABLE", status.HTTP_503_SERVICE_UNAVAILABLE)

    try:
        payload = response.json()
    except ValueError:
        return None, _error("Сервис товаров временно недоступен, попробуйте позже", "B2B_UNAVAILABLE", status.HTTP_503_SERVICE_UNAVAILABLE)

    return payload.get("items", []), None


def _catalog_products_by_ids(product_ids):
    if not product_ids:
        return [], None
    return _catalog_request({"ids": ",".join(str(item) for item in product_ids)})


def _catalog_products_by_sku_ids(sku_ids):
    if not sku_ids:
        return [], None
    return _catalog_request({"sku_ids": ",".join(str(item) for item in sku_ids)})


def _product_map_by_id(products):
    return {str(product["id"]): product for product in products}


def _sku_map(products):
    result = {}
    for product in products:
        for sku in product.get("skus", []):
            result[str(sku["id"])] = (product, sku)
    return result


def _get_or_create_cart(user_id, session_id):
    if user_id:
        return Cart.objects.get_or_create(user_id=user_id, defaults={"session_id": None})
    return Cart.objects.get_or_create(session_id=session_id, defaults={"user_id": None})


def _merge_session_cart_into_user_cart(user_id, session_id):
    if not user_id or not session_id:
        return
    session_cart = Cart.objects.filter(session_id=session_id).first()
    if not session_cart:
        return
    user_cart, _ = Cart.objects.get_or_create(user_id=user_id, defaults={"session_id": None})
    for guest_item in session_cart.items.all():
        target_item = user_cart.items.filter(sku_id=guest_item.sku_id).first()
        if target_item:
            changed = False
            merged_quantity = max(target_item.quantity, guest_item.quantity)
            if target_item.quantity != merged_quantity:
                target_item.quantity = merged_quantity
                changed = True
            if guest_item.product_id and target_item.product_id != guest_item.product_id:
                target_item.product_id = guest_item.product_id
                changed = True
            if changed:
                target_item.save(update_fields=["quantity", "product_id", "updated_at"])
        else:
            guest_item.cart = user_cart
            guest_item.save(update_fields=["cart"])
    session_cart.delete()


def _effective_cart(user_id, session_id):
    if user_id and session_id:
        _merge_session_cart_into_user_cart(user_id, session_id)
    if user_id:
        return Cart.objects.filter(user_id=user_id).first()
    return Cart.objects.filter(session_id=session_id).first()


def _serialize_enriched_favorite(favorite, product):
    return {"product": product, "added_at": favorite.added_at}


def _serialize_cart_item(item, product, sku):
    unavailable_reason = None
    available_stock = 0
    unit_price = 0
    line_total = 0
    product_title = None
    sku_name = None
    image_url = None

    if product and sku:
        product_title = product.get("title")
        sku_name = sku.get("name")
        image_url = sku.get("image") or product.get("images", [{}])[0].get("url") if product.get("images") else sku.get("image")
        available_stock = int(sku.get("active_quantity") or 0)
        unit_price = int(sku.get("price") or 0) - int(sku.get("discount") or 0)
        product_status = (product.get("status") or "").upper()
        if product_status == "BLOCKED":
            unavailable_reason = CartItem.UnavailableReason.PRODUCT_BLOCKED
        elif product_status in {"ON_MODERATION", "PENDING_MODERATION"}:
            unavailable_reason = CartItem.UnavailableReason.ON_MODERATION
        elif available_stock <= 0:
            unavailable_reason = CartItem.UnavailableReason.OUT_OF_STOCK
        elif available_stock < int(item.quantity):
            unavailable_reason = CartItem.UnavailableReason.OUT_OF_STOCK
        else:
            line_total = unit_price * int(item.quantity)
    else:
        unavailable_reason = CartItem.UnavailableReason.PRODUCT_DELETED

    is_available = unavailable_reason is None
    image_ref = None
    if image_url:
        image_ref = {"id": str(item.sku_id), "url": image_url, "ordering": 0, "is_main": True}
    display_name = sku_name or product_title or "Товар"
    if product_title and sku_name and sku_name not in product_title:
        display_name = f"{product_title} — {sku_name}"

    return {
        "sku_id": str(item.sku_id),
        "product_id": str(item.product_id) if item.product_id else (product.get("id") if product else None),
        "name": display_name,
        "sku_code": sku.get("sku_code") if sku else None,
        "quantity": item.quantity,
        "unit_price": unit_price,
        "unit_price_at_add": unit_price,
        "line_total": line_total,
        "available_quantity": available_stock,
        "is_available": is_available,
        "image": image_ref,
        # Legacy fields kept for backward compatibility in tests/clients
        "item_id": str(item.id),
        "product_title": product_title,
        "sku_name": sku_name,
        "available": is_available,
        "available_stock": available_stock,
        "unavailable_reason": unavailable_reason,
        "updated_at": item.updated_at,
    }


def _empty_cart_payload(cart=None):
    return {
        "id": str(cart.id) if cart else None,
        "items": [],
        "items_count": 0,
        "subtotal": 0,
        "is_valid": True,
        "updated_at": cart.updated_at if cart else None,
        "summary": {
            "total_amount": 0,
            "total_items": 0,
            "total_quantity": 0,
            "available_items": 0,
            "unavailable_count": 0,
            "has_unavailable_items": False,
            "checkout_ready": False,
            "currency": "RUB",
        },
        "checkout_payload": {"items": [], "total_amount": 0, "currency": "RUB"},
    }


def _build_cart_payload(cart):
    items = list(cart.items.all().order_by("created_at"))
    if not items:
        return _empty_cart_payload(cart), None

    products, error = _catalog_products_by_sku_ids([item.sku_id for item in items])
    if error:
        return None, error
    sku_context = _sku_map(products)

    serialized = []
    subtotal = 0
    unavailable_count = 0
    for item in items:
        product, sku = sku_context.get(str(item.sku_id), (None, None))
        if product and item.product_id != _parse_uuid(product.get("id")):
            item.product_id = _parse_uuid(product.get("id"))
            item.save(update_fields=["product_id", "updated_at"])
        payload = _serialize_cart_item(item, product, sku)
        if payload["is_available"]:
            subtotal += payload["line_total"]
        else:
            unavailable_count += 1
        serialized.append(payload)

    checkout_items = [
        {"sku_id": item["sku_id"], "quantity": item["quantity"]}
        for item in serialized
        if item["is_available"]
    ]
    total_quantity = sum(item["quantity"] for item in serialized)
    available_items = sum(1 for item in serialized if item["is_available"])
    is_valid = unavailable_count == 0 and bool(serialized)
    payload = {
        "id": str(cart.id),
        "items": serialized,
        "items_count": total_quantity,
        "subtotal": subtotal,
        "is_valid": is_valid,
        "updated_at": cart.updated_at,
        "summary": {
            "total_amount": subtotal,
            "total_items": len(serialized),
            "total_quantity": total_quantity,
            "available_items": available_items,
            "unavailable_count": unavailable_count,
            "has_unavailable_items": unavailable_count > 0,
            "checkout_ready": bool(checkout_items) and is_valid,
            "currency": "RUB",
        },
        "checkout_payload": {"items": checkout_items, "total_amount": subtotal, "currency": "RUB"},
    }
    return payload, None


def _lookup_visible_sku(sku_id):
    products, error = _catalog_products_by_sku_ids([sku_id])
    if error:
        return None, None, error
    context = _sku_map(products)
    product, sku = context.get(str(sku_id), (None, None))
    return product, sku, None


def _require_service_key(request):
    expected = settings.INTERNAL_SERVICE_KEY
    if expected and request.headers.get("X-Service-Key") != expected:
        return _error("Неверный X-Service-Key", "UNAUTHORIZED", status.HTTP_401_UNAUTHORIZED)
    return None


@extend_schema_view(
    get=extend_schema(operation_id="cart_get_cart", responses=OpenApiTypes.OBJECT),
    delete=extend_schema(operation_id="cart_clear_cart", responses=None),
)
class CartView(APIView):
    def get(self, request):
        user_id, session_id, error = _get_cart_identity(request)
        if error:
            return error

        cart = _effective_cart(user_id, session_id)
        if not cart:
            return Response(_empty_cart_payload())

        payload, error = _build_cart_payload(cart)
        if error:
            return error
        return Response(payload)

    def delete(self, request):
        user_id, session_id, error = _get_cart_identity(request)
        if error:
            return error

        cart = _effective_cart(user_id, session_id)
        if cart:
            cart.items.all().delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema_view(
    post=extend_schema(
        operation_id="cart_add_item",
        request=AddCartItemRequestSerializer,
        responses=OpenApiTypes.OBJECT,
    ),
)
class CartItemsView(APIView):
    @transaction.atomic
    def post(self, request):
        user_id, session_id, error = _get_cart_identity(request)
        if error:
            return error
        if user_id and session_id:
            _merge_session_cart_into_user_cart(user_id, session_id)

        serializer = AddCartItemRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return _error("Невалидный запрос", "INVALID_REQUEST", status.HTTP_400_BAD_REQUEST)

        product, sku, lookup_error = _lookup_visible_sku(serializer.validated_data["sku_id"])
        if lookup_error:
            return lookup_error
        if not sku or not product:
            return _error("SKU не найден или недоступен", "SKU_NOT_FOUND", status.HTTP_404_NOT_FOUND)

        cart, _ = _get_or_create_cart(user_id, session_id)
        item, created = CartItem.objects.select_for_update().get_or_create(
            cart=cart,
            sku_id=serializer.validated_data["sku_id"],
            defaults={
                "quantity": serializer.validated_data["quantity"],
                "product_id": _parse_uuid(product.get("id")),
                "unavailable_reason": None,
            },
        )

        new_quantity = serializer.validated_data["quantity"] if created else item.quantity + serializer.validated_data["quantity"]
        available_stock = int(sku.get("active_quantity") or 0)
        if available_stock < new_quantity:
            reason = "OUT_OF_STOCK" if available_stock == 0 else "INSUFFICIENT_STOCK"
            return Response(
                {
                    "code": "INSUFFICIENT_STOCK",
                    "message": "Не удалось добавить товар в корзину",
                    "failed_items": [
                        {
                            "sku_id": str(item.sku_id),
                            "requested": new_quantity,
                            "available": available_stock,
                            "reason": reason,
                        }
                    ],
                },
                status=status.HTTP_409_CONFLICT,
            )

        if not created:
            item.quantity = new_quantity
            item.product_id = _parse_uuid(product.get("id"))
            item.unavailable_reason = None
            item.save(update_fields=["quantity", "product_id", "unavailable_reason", "updated_at"])

        cart_payload, build_error = _build_cart_payload(cart)
        if build_error:
            return build_error
        return Response(cart_payload, status=status.HTTP_200_OK)


@extend_schema_view(
    get=extend_schema(operation_id="cart_get_item", responses=CartItemSerializer),
    put=extend_schema(
        operation_id="cart_update_item",
        request=UpdateCartItemRequestSerializer,
        responses=OpenApiTypes.OBJECT,
    ),
    delete=extend_schema(operation_id="cart_delete_item", responses=None),
)
class CartItemDetailView(APIView):
    def _resolve_item(self, request, sku_id):
        user_id, session_id, error = _get_cart_identity(request)
        if error:
            return None, None, error
        if user_id and session_id:
            _merge_session_cart_into_user_cart(user_id, session_id)

        cart = _effective_cart(user_id, session_id)
        if not cart:
            return None, None, _error("Позиция не найдена в корзине", "CART_ITEM_NOT_FOUND", status.HTTP_404_NOT_FOUND)

        item = CartItem.objects.select_related("cart").filter(cart=cart, sku_id=sku_id).first()
        if not item:
            return None, None, _error("Позиция не найдена в корзине", "CART_ITEM_NOT_FOUND", status.HTTP_404_NOT_FOUND)

        return item, cart, None

    @transaction.atomic
    def patch(self, request, sku_id):
        item, cart, error = self._resolve_item(request, sku_id)
        if error:
            return error

        serializer = UpdateCartItemRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return _error("Невалидный запрос", "INVALID_REQUEST", status.HTTP_400_BAD_REQUEST)

        product, sku, lookup_error = _lookup_visible_sku(item.sku_id)
        if lookup_error:
            return lookup_error
        if not product or not sku:
            return _error("SKU не найден или недоступен", "SKU_NOT_FOUND", status.HTTP_404_NOT_FOUND)
        available_stock = int(sku.get("active_quantity") or 0)
        if available_stock < serializer.validated_data["quantity"]:
            reason = "OUT_OF_STOCK" if available_stock == 0 else "INSUFFICIENT_STOCK"
            return Response(
                {
                    "code": "INSUFFICIENT_STOCK",
                    "message": "Не удалось обновить количество",
                    "failed_items": [
                        {
                            "sku_id": str(item.sku_id),
                            "requested": serializer.validated_data["quantity"],
                            "available": available_stock,
                            "reason": reason,
                        }
                    ],
                },
                status=status.HTTP_409_CONFLICT,
            )

        item.quantity = serializer.validated_data["quantity"]
        item.product_id = _parse_uuid(product.get("id"))
        item.unavailable_reason = None
        item.save(update_fields=["quantity", "product_id", "unavailable_reason", "updated_at"])

        cart_payload, build_error = _build_cart_payload(cart)
        if build_error:
            return build_error
        return Response(cart_payload)

    put = patch

    def delete(self, request, sku_id):
        item, cart, error = self._resolve_item(request, sku_id)
        if error:
            return error

        item.delete()
        cart_payload, build_error = _build_cart_payload(cart)
        if build_error:
            return build_error
        return Response(cart_payload)


@extend_schema_view(
    get=extend_schema(operation_id="cart_validate", responses=OpenApiTypes.OBJECT),
)
class CartValidateView(APIView):
    def post(self, request):
        user_id, session_id, error = _get_cart_identity(request)
        if error:
            return error

        cart = _effective_cart(user_id, session_id)
        if not cart:
            empty = _empty_cart_payload()
            return Response({"is_valid": True, "cart": empty, "issues": []})

        payload, build_error = _build_cart_payload(cart)
        if build_error:
            return build_error
        issues = []
        for item in payload["items"]:
            if item.get("unavailable_reason"):
                issues.append(
                    {
                        "sku_id": item["sku_id"],
                        "type": item["unavailable_reason"],
                        "message": "Товар недоступен для оформления",
                    }
                )
            elif item["available_quantity"] < item["quantity"]:
                issues.append(
                    {
                        "sku_id": item["sku_id"],
                        "type": "QUANTITY_REDUCED",
                        "message": "Недостаточно остатка для выбранного количества",
                        "old_value": item["quantity"],
                        "new_value": item["available_quantity"],
                    }
                )

        return Response(
            {
                "is_valid": len(issues) == 0 and payload.get("is_valid", False),
                "cart": payload,
                "issues": issues,
            }
        )

    get = post


class CartMergeView(APIView):
    def post(self, request):
        user_id, session_id, error = _get_cart_identity(request)
        if error:
            return error
        if not user_id:
            return _error("Требуется авторизация", "UNAUTHORIZED", status.HTTP_401_UNAUTHORIZED)
        if not session_id:
            return _error("Передайте X-Session-Id", "MISSING_CART_IDENTITY", status.HTTP_400_BAD_REQUEST)

        _merge_session_cart_into_user_cart(user_id, session_id)
        cart = Cart.objects.filter(user_id=user_id).first()
        if not cart:
            return Response(_empty_cart_payload())
        payload, build_error = _build_cart_payload(cart)
        if build_error:
            return build_error
        return Response(payload)


@extend_schema_view(
    get=extend_schema(operation_id="favorites_list", responses=OpenApiTypes.OBJECT),
)
class FavoritesView(APIView):
    def get(self, request):
        user_id, error = _get_user_id_for_favorites(request)
        if error:
            return error

        try:
            limit = max(1, min(int(request.query_params.get("limit", 20)), 100))
            offset = max(0, int(request.query_params.get("offset", 0)))
        except ValueError:
            return _error("Параметры limit/offset невалидны", "INVALID_PARAMETER", status.HTTP_400_BAD_REQUEST)

        favorites = list(Favorite.objects.filter(user_id=user_id).order_by("-added_at"))
        product_ids = [favorite.product_id for favorite in favorites]
        products, catalog_error = _catalog_products_by_ids(product_ids)
        if catalog_error:
            return catalog_error
        products_by_id = _product_map_by_id(products)
        visible_favorites = [
            favorite for favorite in favorites if str(favorite.product_id) in products_by_id
        ]
        page = visible_favorites[offset : offset + limit]
        items = [
            _serialize_enriched_favorite(favorite, products_by_id[str(favorite.product_id)])
            for favorite in page
        ]
        total = len(visible_favorites)

        return Response({"items": items, "total": total, "total_count": total, "limit": limit, "offset": offset})


@extend_schema_view(
    post=extend_schema(operation_id="favorites_add", responses=FavoriteMutationSerializer),
    delete=extend_schema(operation_id="favorites_delete", responses=None),
)
class FavoriteDetailView(APIView):
    serializer_class = FavoriteMutationSerializer

    def put(self, request, product_id):
        return self._add(request, product_id)

    def post(self, request, product_id):
        return self._add(request, product_id)

    def _add(self, request, product_id):
        user_id, error = _get_user_id_for_favorites(request)
        if error:
            return error

        product_uuid = _parse_uuid(product_id)
        if not product_uuid:
            return _error("Некорректный UUID product_id", "INVALID_PARAMETER", status.HTTP_400_BAD_REQUEST)

        products, catalog_error = _catalog_products_by_ids([product_uuid])
        if catalog_error:
            return catalog_error
        if not products:
            return _error("Товар не найден", "PRODUCT_NOT_FOUND", status.HTTP_404_NOT_FOUND)

        favorite, created = Favorite.objects.get_or_create(user_id=user_id, product_id=product_uuid)
        if created:
            return Response(
                _favorite_mutation_payload(favorite, "Товар добавлен в избранное"),
                status=status.HTTP_201_CREATED,
            )
        return Response(
            _favorite_mutation_payload(favorite, "Товар уже находится в избранном"),
            status=status.HTTP_200_OK,
        )

    def delete(self, request, product_id):
        user_id, error = _get_user_id_for_favorites(request)
        if error:
            return error

        product_uuid = _parse_uuid(product_id)
        if not product_uuid:
            return _error("Некорректный UUID product_id", "INVALID_PARAMETER", status.HTTP_400_BAD_REQUEST)

        Favorite.objects.filter(user_id=user_id, product_id=product_uuid).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema_view(
    post=extend_schema(
        operation_id="favorites_subscribe",
        request=SubscribeRequestSerializer,
        responses=OpenApiTypes.OBJECT,
    ),
)
class FavoriteSubscribeView(APIView):
    serializer_class = SubscribeRequestSerializer

    def post(self, request, product_id):
        user_id, error = _get_user_id_for_favorites(request)
        if error:
            return error

        product_uuid = _parse_uuid(product_id)
        if not product_uuid:
            return _error("Некорректный UUID product_id", "INVALID_PARAMETER", status.HTTP_400_BAD_REQUEST)

        serializer = SubscribeRequestSerializer(data=request.data or {})
        if not serializer.is_valid():
            return _error("Должен быть указан хотя бы один тип уведомления", "INVALID_NOTIFY_ON", status.HTTP_400_BAD_REQUEST)

        products, catalog_error = _catalog_products_by_ids([product_uuid])
        if catalog_error:
            return catalog_error
        if not products:
            return _error("Товар не найден", "PRODUCT_NOT_FOUND", status.HTTP_404_NOT_FOUND)

        if Subscription.objects.filter(user_id=user_id, product_id=product_uuid).exists():
            return _error(
                "Вы уже подписаны на уведомления об этом товаре",
                "SUBSCRIPTION_ALREADY_EXISTS",
                status.HTTP_409_CONFLICT,
            )

        subscription = Subscription.objects.create(
            user_id=user_id,
            product_id=product_uuid,
            notify_on=serializer.validated_data["notify_on"],
        )
        return Response(
            _serialize_subscription_response(subscription, products[0]),
            status=status.HTTP_201_CREATED,
        )

    def delete(self, request, product_id):
        user_id, error = _get_user_id_for_favorites(request)
        if error:
            return error

        product_uuid = _parse_uuid(product_id)
        if not product_uuid:
            return _error("Некорректный UUID product_id", "INVALID_PARAMETER", status.HTTP_400_BAD_REQUEST)

        Subscription.objects.filter(user_id=user_id, product_id=product_uuid).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


def _banner_events_validation_error(serializer):
    events_errors = serializer.errors.get("events")
    if events_errors:
        if any(err and "empty" in str(err).lower() for err in events_errors):
            return _error("Массив events не может быть пустым", "EMPTY_EVENTS", status.HTTP_400_BAD_REQUEST)
        for entry in events_errors:
            if isinstance(entry, dict) and "event" in entry:
                return _error(
                    "Допустимые значения event: impression, click",
                    "INVALID_EVENT_TYPE",
                    status.HTTP_400_BAD_REQUEST,
                )
    return _error("Массив events не может быть пустым", "EMPTY_EVENTS", status.HTTP_400_BAD_REQUEST)


@extend_schema_view(
    post=extend_schema(operation_id="postBannerEvents", request=BannerEventsRequestSerializer, responses=None),
)
class BannerEventsView(APIView):
    @transaction.atomic
    def post(self, request):
        serializer = BannerEventsRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return _banner_events_validation_error(serializer)

        user_id, _session_id, _token_error = _extract_identity(request)
        allowed_banner_ids = set(str(item) for item in Banner.objects.values_list("id", flat=True))
        rows = []

        for event in serializer.validated_data["events"]:
            banner_id = str(event["banner_id"])
            if banner_id not in allowed_banner_ids:
                return _error("Баннер с указанным id не найден", "BANNER_NOT_FOUND", status.HTTP_400_BAD_REQUEST)
            rows.append(
                BannerEvent(
                    banner_id=event["banner_id"],
                    user_id=user_id,
                    event=event["event"],
                    timestamp=event["timestamp"],
                )
            )

        BannerEvent.objects.bulk_create(rows)
        return Response(status=status.HTTP_204_NO_CONTENT)


def _serialize_collection_metadata(collection):
    start_date = collection.start_date or collection.created_at.date()
    return {
        "id": str(collection.id),
        "title": collection.title,
        "description": collection.description,
        "cover_image_url": collection.cover_image_url,
        "target_url": collection.target_url,
        "priority": collection.priority,
        "start_date": start_date.isoformat(),
    }


@extend_schema_view(
    get=extend_schema(operation_id="getCollections", responses=OpenApiTypes.OBJECT),
)
class MainCollectionsView(APIView):
    def get(self, request):
        limit = max(1, min(_parse_int(request.query_params.get("limit", 10), 10), 50))
        offset = max(0, _parse_int(request.query_params.get("offset", 0), 0))
        today = timezone.now().date()
        queryset = (
            Collection.objects.filter(is_active=True)
            .filter(Q(start_date__isnull=True) | Q(start_date__lte=today))
            .order_by("priority", "-created_at")
        )
        total_count = queryset.count()
        collections = queryset[offset : offset + limit]
        return Response(
            {
                "metadata": {"total_count": total_count, "limit": limit, "offset": offset},
                "collections": [_serialize_collection_metadata(collection) for collection in collections],
            }
        )


@extend_schema_view(
    get=extend_schema(operation_id="getCollectionProducts", responses=OpenApiTypes.OBJECT),
)
class CollectionProductsView(APIView):
    def get(self, request, collection_id):
        collection = Collection.objects.filter(id=collection_id, is_active=True).first()
        if not collection:
            return _error("Подборка не найдена", "COLLECTION_NOT_FOUND", status.HTTP_404_NOT_FOUND)
        limit = max(1, min(_parse_int(request.query_params.get("limit", 20), 20), 100))
        offset = max(0, _parse_int(request.query_params.get("offset", 0), 0))
        product_links = list(collection.collection_products.all().order_by("ordering", "product_id"))
        product_ids = [link.product_id for link in product_links]
        paged_ids = product_ids[offset : offset + limit]
        products, catalog_error = _catalog_products_by_ids(paged_ids)
        if catalog_error:
            return catalog_error
        products_by_id = _product_map_by_id(products)
        items = [products_by_id[str(product_id)] for product_id in paged_ids if str(product_id) in products_by_id]
        unavailable_ids = [str(product_id) for product_id in paged_ids if str(product_id) not in products_by_id]
        return Response(
            {
                "collection_title": collection.title,
                "total_products": len(product_ids),
                "items": items,
                "unavailable_ids": unavailable_ids,
            }
        )


@extend_schema_view(
    get=extend_schema(operation_id="getHomeBanners", responses=OpenApiTypes.OBJECT),
)
class HomeBannersView(APIView):
    def get(self, request):
        now = timezone.now()
        queryset = (
            Banner.objects.filter(is_active=True, placement=Banner.Placement.HOME)
            .filter(Q(start_at__isnull=True) | Q(start_at__lte=now))
            .filter(Q(end_at__isnull=True) | Q(end_at__gte=now))
            .order_by("priority", "-created_at")
        )
        items = [
            {
                "id": str(banner.id),
                "title": banner.title,
                "image_url": banner.image_url,
                "link": banner.link,
                "priority": banner.priority,
            }
            for banner in queryset
        ]
        return Response({"items": items, "total_count": len(items)})


@extend_schema_view(
    get=extend_schema(operation_id="cart_get_also_bought", responses=OpenApiTypes.OBJECT),
)
class AlsoBoughtView(APIView):
    def get(self, request):
        products, error = _catalog_request({"limit": 12, "offset": 0, "sort": "date_desc"})
        if error:
            return error
        return Response({"items": products[:6], "total": min(6, len(products))})


@extend_schema_view(
    post=extend_schema(operation_id="cart_process_product_event", request=OpenApiTypes.OBJECT, responses=OpenApiTypes.OBJECT),
)
class ProductEventsView(APIView):
    @transaction.atomic
    def post(self, request):
        error = _require_service_key(request)
        if error:
            return error

        payload = request.data or {}
        idempotency_key = str(payload.get("idempotency_key") or "").strip()
        event = str(payload.get("event") or "").strip()
        product_id = _parse_uuid(payload.get("product_id"))
        sku_ids = [_parse_uuid(item) for item in payload.get("sku_ids", [])]
        if not idempotency_key or not event or not product_id or not sku_ids or any(item is None for item in sku_ids):
            return _error("Невалидный payload события", "INVALID_REQUEST", status.HTTP_400_BAD_REQUEST)

        if ProductEventInbox.objects.filter(idempotency_key=idempotency_key).exists():
            return Response({"accepted": True})

        reason_map = {
            "PRODUCT_BLOCKED": CartItem.UnavailableReason.PRODUCT_BLOCKED,
            "PRODUCT_DELETED": CartItem.UnavailableReason.PRODUCT_DELETED,
            "SKU_OUT_OF_STOCK": CartItem.UnavailableReason.OUT_OF_STOCK,
            "OUT_OF_STOCK": CartItem.UnavailableReason.OUT_OF_STOCK,
        }
        unavailable_reason = reason_map.get(event)
        if not unavailable_reason:
            return _error(
                "Неподдерживаемый тип события",
                "INVALID_EVENT",
                status.HTTP_400_BAD_REQUEST,
            )
        CartItem.objects.filter(sku_id__in=sku_ids).update(unavailable_reason=unavailable_reason)

        ProductEventInbox.objects.create(
            idempotency_key=idempotency_key,
            event=event,
            product_id=product_id,
            sku_ids=[str(item) for item in sku_ids],
            reason=payload.get("reason"),
        )
        return Response({"accepted": True})


class B2BEventsView(APIView):
    """Canonical /api/v1/b2b/events adapter for unified B2C OpenAPI."""

    @transaction.atomic
    def post(self, request):
        error = _require_service_key(request)
        if error:
            return error

        payload = request.data or {}
        event_type = str(payload.get("event_type") or "").strip()
        idempotency_key = str(payload.get("idempotency_key") or "").strip()
        inner = payload.get("payload") or {}
        if not event_type or not idempotency_key:
            return _error("Невалидный payload события", "INVALID_REQUEST", status.HTTP_400_BAD_REQUEST)

        if ProductEventInbox.objects.filter(idempotency_key=idempotency_key).exists():
            return Response(status=status.HTTP_409_CONFLICT)

        product_id = _parse_uuid(inner.get("product_id"))
        sku_id = _parse_uuid(inner.get("sku_id"))
        sku_ids = [str(sku_id)] if sku_id else []
        if product_id and not sku_ids:
            sku_ids = [
                str(row)
                for row in CartItem.objects.filter(product_id=product_id).values_list("sku_id", flat=True).distinct()
            ]
        if not product_id or not sku_ids:
            return _error("Невалидный payload события", "INVALID_REQUEST", status.HTTP_400_BAD_REQUEST)

        reason_map = {
            "PRODUCT_BLOCKED": CartItem.UnavailableReason.PRODUCT_BLOCKED,
            "PRODUCT_HARD_BLOCKED": CartItem.UnavailableReason.PRODUCT_BLOCKED,
            "PRODUCT_DELETED": CartItem.UnavailableReason.PRODUCT_DELETED,
            "SKU_OUT_OF_STOCK": CartItem.UnavailableReason.OUT_OF_STOCK,
        }
        unavailable_reason = reason_map.get(event_type)
        if unavailable_reason:
            CartItem.objects.filter(sku_id__in=sku_ids).update(unavailable_reason=unavailable_reason)

        ProductEventInbox.objects.create(
            idempotency_key=idempotency_key,
            event=event_type,
            product_id=product_id,
            sku_ids=sku_ids,
            reason=inner.get("reason"),
        )
        return Response(status=status.HTTP_202_ACCEPTED)
