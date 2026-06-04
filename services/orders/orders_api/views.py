import logging
from uuid import UUID

import jwt
import requests
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, extend_schema_view
from jwt import InvalidTokenError
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import IdempotencyKey, IntegrationOutbox, Order, OrderItem
from .serializers import CancelOrderRequestSerializer, CreateOrderRequestSerializer, OrderListItemSerializer, OrderSerializer, UpdateOrderStatusRequestSerializer


logger = logging.getLogger(__name__)

CANCELABLE_STATUSES = {Order.Status.CREATED, Order.Status.PAID}

ALLOWED_TRANSITIONS = {
    Order.Status.CREATED: {Order.Status.PAID, Order.Status.CANCELED},
    Order.Status.PENDING: {Order.Status.PAID, Order.Status.CANCELED},
    Order.Status.PAID: {Order.Status.ASSEMBLING, Order.Status.CANCELED},
    Order.Status.ASSEMBLING: {Order.Status.DELIVERING},
    Order.Status.DELIVERING: {Order.Status.DELIVERED},
    Order.Status.DELIVERED: set(),
    Order.Status.CANCELED: set(),
    Order.Status.CANCEL_PENDING: {Order.Status.CANCELED},
}


def _parse_uuid(value):
    if not value:
        return None
    try:
        return UUID(str(value))
    except (ValueError, TypeError):
        return None


def _error(code, message, http_status):
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
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth.split(" ", 1)[1].strip()
        payload = _decode_jwt_payload(token)
        if payload is None:
            return None, False, True
        user_candidate = payload.get("sub") or payload.get("user_id")
        user_id = _parse_uuid(user_candidate)
        roles = payload.get("roles", [])
        if isinstance(roles, str):
            roles = [roles]
        is_admin = any(role.upper() == "ADMIN" for role in roles) or bool(payload.get("is_admin"))
        if user_id:
            return user_id, is_admin, False
        return None, False, True

    user_id = _parse_uuid(request.headers.get("X-User-Id"))
    is_admin = request.headers.get("X-Admin", "false").lower() == "true"
    return user_id, is_admin, False


def _get_user_id(request):
    user_id, _is_admin, token_error = _extract_identity(request)
    if token_error:
        return None, _error("UNAUTHORIZED", "Требуется авторизация", status.HTTP_401_UNAUTHORIZED)
    if not user_id:
        return None, _error("UNAUTHORIZED", "Требуется авторизация", status.HTTP_401_UNAUTHORIZED)
    return user_id, None


def _is_admin(request):
    _user_id, is_admin, _token_error = _extract_identity(request)
    return is_admin


def _catalog_products_by_sku_ids(sku_ids):
    if not sku_ids:
        return [], None
    try:
        response = requests.get(
            settings.CATALOG_PRODUCTS_URL,
            params={"sku_ids": ",".join(str(item) for item in sku_ids)},
            headers={"X-Service-Key": settings.INTERNAL_SERVICE_KEY},
            timeout=settings.CATALOG_TIMEOUT,
        )
    except requests.RequestException:
        return None, _error("B2B_UNAVAILABLE", "Сервис товаров временно недоступен, попробуйте позже", status.HTTP_503_SERVICE_UNAVAILABLE)
    if response.status_code != status.HTTP_200_OK:
        return None, _error("B2B_UNAVAILABLE", "Сервис товаров временно недоступен, попробуйте позже", status.HTTP_503_SERVICE_UNAVAILABLE)
    try:
        payload = response.json()
    except ValueError:
        return None, _error("B2B_UNAVAILABLE", "Сервис товаров временно недоступен, попробуйте позже", status.HTTP_503_SERVICE_UNAVAILABLE)
    return payload.get("items", []), None


def _inventory_call(url, payload):
    try:
        response = requests.post(
            url,
            json=payload,
            headers={"X-Service-Key": settings.INTERNAL_SERVICE_KEY},
            timeout=settings.B2B_TIMEOUT,
        )
    except requests.RequestException:
        return None, "unavailable"
    try:
        data = response.json()
    except ValueError:
        data = {}
    return (response.status_code, data), None


def _normalize_promo_code(raw):
    if not raw:
        return ''
    s = str(raw).strip()
    if s.upper() == 'ПОЛИНА' or s.casefold() == 'polina':
        return 'POLINA'
    return s.upper()


def _apply_promo_to_amount(promo_code, gross_amount):
    if not promo_code:
        return gross_amount, None
    try:
        response = requests.post(
            settings.PROMO_APPLY_URL,
            json={'code': promo_code, 'amount': int(gross_amount)},
            timeout=3.0,
        )
    except requests.RequestException:
        return None, _error('PROMO_UNAVAILABLE', 'Промо-сервис временно недоступен', status.HTTP_503_SERVICE_UNAVAILABLE)
    try:
        body = response.json()
    except ValueError:
        return None, _error('PROMO_UNAVAILABLE', 'Промо-сервис временно недоступен', status.HTTP_503_SERVICE_UNAVAILABLE)
    if response.status_code != status.HTTP_200_OK:
        return None, _error('PROMO_UNAVAILABLE', 'Промо-сервис временно недоступен', status.HTTP_503_SERVICE_UNAVAILABLE)
    if not body.get('valid'):
        return None, Response(
            {'code': 'PROMO_INVALID', 'message': 'Промокод не применён', 'reason': body.get('reason', 'unknown')},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return int(body['final_amount']), None


def _idempotency_key_from_request(request, data):
    header_key = request.headers.get("Idempotency-Key") or request.headers.get("HTTP_IDEMPOTENCY_KEY")
    if header_key:
        return str(header_key)
    if data.get("idempotency_key"):
        return str(data["idempotency_key"])
    return None


def _cart_items_from_service(request):
    headers = {}
    auth = request.headers.get("Authorization")
    if auth:
        headers["Authorization"] = auth
    session_id = request.headers.get("X-Session-Id")
    if session_id:
        headers["X-Session-Id"] = session_id
    try:
        response = requests.get(settings.CART_URL, headers=headers, timeout=settings.CATALOG_TIMEOUT)
    except requests.RequestException:
        return None, _error("CART_UNAVAILABLE", "Сервис корзины временно недоступен", status.HTTP_503_SERVICE_UNAVAILABLE)
    if response.status_code != status.HTTP_200_OK:
        return None, _error("CART_UNAVAILABLE", "Сервис корзины временно недоступен", status.HTTP_503_SERVICE_UNAVAILABLE)
    try:
        payload = response.json()
    except ValueError:
        return None, _error("CART_UNAVAILABLE", "Сервис корзины временно недоступен", status.HTTP_503_SERVICE_UNAVAILABLE)
    items = []
    for row in payload.get("items", []):
        if row.get("is_available", row.get("available", False)):
            items.append({"sku_id": row["sku_id"], "quantity": row["quantity"]})
    if not items:
        return None, _error("INVALID_REQUEST", "Корзина пуста или содержит недоступные позиции", status.HTTP_422_UNPROCESSABLE_ENTITY)
    return items, None


def _normalize_items(items):
    aggregated = {}
    for item in items:
        sku_id = str(item["sku_id"])
        aggregated[sku_id] = aggregated.get(sku_id, 0) + int(item["quantity"])
    return [{"sku_id": key, "quantity": value} for key, value in aggregated.items()]


def _sku_context(products):
    context = {}
    for product in products:
        for sku in product.get("skus", []):
            context[str(sku["id"])] = (product, sku)
    return context


def _outbox_event(order_id, event_type, payload):
    IntegrationOutbox.objects.create(aggregate_id=order_id, event_type=event_type, payload=payload)


@extend_schema_view(
    post=extend_schema(operation_id="createOrder", request=CreateOrderRequestSerializer, responses=OrderSerializer),
    get=extend_schema(operation_id="listOrders", responses=OpenApiTypes.OBJECT),
)
class OrdersView(APIView):
    @transaction.atomic
    def post(self, request):
        user_id, error = _get_user_id(request)
        if error:
            return error

        serializer = CreateOrderRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return _error("INVALID_REQUEST", "Invalid request payload", status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        idem_key = _idempotency_key_from_request(request, data)
        if not idem_key:
            return _error("INVALID_REQUEST", "Idempotency-Key header is required", status.HTTP_400_BAD_REQUEST)

        existing = IdempotencyKey.objects.filter(key=idem_key, user_id=user_id).select_related("order").first()
        if existing:
            return Response(OrderSerializer(existing.order).data, status=status.HTTP_200_OK)

        if data.get("items"):
            items = _normalize_items(data["items"])
        else:
            items, cart_error = _cart_items_from_service(request)
            if cart_error:
                return cart_error
        products, catalog_error = _catalog_products_by_sku_ids([item["sku_id"] for item in items])
        if catalog_error:
            return catalog_error
        context = _sku_context(products)

        failed_items = []
        prepared_items = []
        total_amount = 0
        for item in items:
            product, sku = context.get(str(item["sku_id"]), (None, None))
            if not product or not sku:
                failed_items.append({"sku_id": str(item["sku_id"]), "requested": item["quantity"], "available": 0, "reason": "SKU_NOT_FOUND"})
                continue
            available = int(sku.get("active_quantity") or 0)
            if available < item["quantity"]:
                failed_items.append(
                    {
                        "sku_id": str(item["sku_id"]),
                        "requested": item["quantity"],
                        "available": available,
                        "reason": "OUT_OF_STOCK" if available == 0 else "INSUFFICIENT_STOCK",
                    }
                )
                continue
            unit_price = int(sku.get("price") or 0) - int(sku.get("discount") or 0)
            line_total = unit_price * int(item["quantity"])
            total_amount += line_total
            prepared_items.append(
                {
                    "product_id": product["id"],
                    "product_title": product.get("title", ""),
                    "sku_id": str(item["sku_id"]),
                    "sku_name": sku.get("name", ""),
                    "quantity": int(item["quantity"]),
                    "unit_price_amount": unit_price,
                    "line_total_amount": line_total,
                }
            )

        if failed_items:
            return Response({"code": "RESERVE_FAILED", "message": "Не удалось зарезервировать товары", "failed_items": failed_items}, status=status.HTTP_409_CONFLICT)

        reserve_result, reserve_error = _inventory_call(
            settings.B2B_RESERVE_URL,
            {"idempotency_key": idem_key, "items": [{"sku_id": item["sku_id"], "quantity": item["quantity"]} for item in items]},
        )
        if reserve_error == "unavailable":
            return _error("B2B_UNAVAILABLE", "Сервис товаров временно недоступен, попробуйте позже", status.HTTP_503_SERVICE_UNAVAILABLE)
        reserve_status, reserve_payload = reserve_result
        if reserve_status == status.HTTP_409_CONFLICT:
            failed_items = reserve_payload.get("failed_items")
            if not failed_items:
                failed_items = [
                    {
                        "sku_id": str(item["sku_id"]),
                        "requested": item["quantity"],
                        "available": 0,
                        "reason": reserve_payload.get("code", "RESERVE_FAILED"),
                    }
                    for item in items
                ]
            return Response(
                {
                    "code": "RESERVE_FAILED",
                    "message": "Не удалось зарезервировать товары",
                    "failed_items": failed_items,
                },
                status=status.HTTP_409_CONFLICT,
            )
        if reserve_status != status.HTTP_200_OK:
            return _error("B2B_UNAVAILABLE", "Сервис товаров временно недоступен, попробуйте позже", status.HTTP_503_SERVICE_UNAVAILABLE)

        promo_code = _normalize_promo_code(data.get('promo_code') or '')
        charged_amount = total_amount
        if promo_code:
            charged_amount, promo_error = _apply_promo_to_amount(promo_code, total_amount)
            if promo_error is not None:
                _inventory_call(
                    settings.B2B_UNRESERVE_URL,
                    {
                        'idempotency_key': f'{idem_key}:promo-fail',
                        'items': [{'sku_id': item['sku_id'], 'quantity': item['quantity']} for item in items],
                    },
                )
                return promo_error

        order_number = f"NM-{timezone.now().year}-{Order.objects.count() + 1:06d}"
        order = Order.objects.create(
            user_id=user_id,
            status=Order.Status.PAID,
            number=order_number,
            subtotal_amount=total_amount,
            delivery_cost=0,
            total_amount=charged_amount,
            total_currency="RUB",
            payment_method=Order.PaymentMethod.CARD_ONLINE,
            delivery_address=data.get("delivery_address") or {},
            address_id=data.get("address_id"),
            payment_method_id=data.get("payment_method_id"),
            comment=data.get("comment") or "",
        )
        rows = [
            OrderItem(
                order=order,
                product_id=item["product_id"],
                product_title=item["product_title"],
                sku_id=item["sku_id"],
                sku_name=item["sku_name"],
                quantity=item["quantity"],
                unit_price_amount=item["unit_price_amount"],
                unit_price_currency="RUB",
                line_total_amount=item["line_total_amount"],
                line_total_currency="RUB",
            )
            for item in prepared_items
        ]
        OrderItem.objects.bulk_create(rows)
        try:
            IdempotencyKey.objects.create(key=idem_key, user_id=user_id, order=order)
        except IntegrityError:
            order.delete()
            existing = IdempotencyKey.objects.filter(key=idem_key, user_id=user_id).select_related("order").first()
            if existing:
                return Response(OrderSerializer(existing.order).data, status=status.HTTP_200_OK)
            raise
        _outbox_event(order.id, "ORDER_CREATED", {"order_id": str(order.id), "user_id": str(user_id), "status": order.status, "total_amount": order.total_amount})
        return Response(OrderSerializer(order).data, status=status.HTTP_201_CREATED)

    def get(self, request):
        user_id, error = _get_user_id(request)
        if error:
            return error

        try:
            limit = max(1, min(int(request.query_params.get("limit", 20)), 100))
            offset = max(0, int(request.query_params.get("offset", 0)))
        except ValueError:
            return _error("INVALID_REQUEST", "Invalid pagination parameters", status.HTTP_400_BAD_REQUEST)

        queryset = Order.objects.filter(user_id=user_id).prefetch_related("items")
        status_filter = request.query_params.get("status")
        if status_filter:
            queryset = queryset.filter(status="CANCELED" if status_filter == "CANCELLED" else status_filter)
        total = queryset.count()
        orders = queryset[offset : offset + limit]
        return Response(
            {
                "items": OrderListItemSerializer(orders, many=True).data,
                "total": total,
                "total_count": total,
                "limit": limit,
                "offset": offset,
            }
        )


@extend_schema_view(get=extend_schema(operation_id="getOrder", responses=OrderSerializer))
class OrderDetailView(APIView):
    def get(self, request, order_id):
        user_id, error = _get_user_id(request)
        if error:
            return error

        order = Order.objects.filter(id=order_id, user_id=user_id).prefetch_related("items").first()
        if not order:
            return _error("ORDER_NOT_FOUND", "Заказ не найден", status.HTTP_404_NOT_FOUND)
        return Response(OrderSerializer(order).data)


@extend_schema_view(
    post=extend_schema(operation_id="cancelOrder", request=CancelOrderRequestSerializer, responses=OrderSerializer),
)
class OrderCancelView(APIView):
    @transaction.atomic
    def post(self, request, order_id):
        user_id, error = _get_user_id(request)
        if error:
            return error

        serializer = CancelOrderRequestSerializer(data=request.data or {})
        if not serializer.is_valid():
            return _error("INVALID_REQUEST", "Invalid cancel payload", status.HTTP_400_BAD_REQUEST)

        order = Order.objects.filter(id=order_id, user_id=user_id).prefetch_related("items").first()
        if not order:
            return _error("ORDER_NOT_FOUND", "Заказ не найден", status.HTTP_404_NOT_FOUND)

        if order.status not in CANCELABLE_STATUSES:
            current_status = "CANCELLED" if order.status == Order.Status.CANCELED else order.status
            return Response(
                {
                    "code": "CANCEL_NOT_ALLOWED",
                    "message": f"Отмена невозможна: заказ в статусе {current_status}",
                    "current_status": current_status,
                },
                status=status.HTTP_409_CONFLICT,
            )

        result, unreserve_error = _inventory_call(
            settings.B2B_UNRESERVE_URL,
            {
                "idempotency_key": str(order.id),
                "items": [{"sku_id": str(item.sku_id), "quantity": item.quantity} for item in order.items.all()],
            },
        )
        if unreserve_error == "unavailable":
            logger.warning(
                "B2B unreserve unavailable for order %s; marked CANCEL_PENDING for async retry",
                order.id,
            )
            order.status = Order.Status.CANCEL_PENDING
        else:
            unreserve_status, _payload = result
            if unreserve_status == status.HTTP_200_OK:
                order.status = Order.Status.CANCELED
            else:
                logger.warning(
                    "B2B unreserve returned %s for order %s; marked CANCEL_PENDING for async retry",
                    unreserve_status,
                    order.id,
                )
                order.status = Order.Status.CANCEL_PENDING
        order.cancel_reason = serializer.validated_data.get("reason") or order.cancel_reason
        order.save(update_fields=["status", "cancel_reason", "updated_at"])
        event_type = "ORDER_CANCEL_PENDING" if order.status == Order.Status.CANCEL_PENDING else "ORDER_CANCELED"
        _outbox_event(
            order.id,
            event_type,
            {"order_id": str(order.id), "reason": order.cancel_reason or "", "status": order.status},
        )
        return Response(OrderSerializer(order).data)


@extend_schema_view(
    patch=extend_schema(operation_id="orders_update_status", request=UpdateOrderStatusRequestSerializer, responses=OrderSerializer),
)
class OrderStatusView(APIView):
    @transaction.atomic
    def patch(self, request, order_id):
        _user_id, error = _get_user_id(request)
        if error:
            return error
        if not _is_admin(request):
            return _error("FORBIDDEN", "Admin role required", status.HTTP_403_FORBIDDEN)

        serializer = UpdateOrderStatusRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return _error("INVALID_REQUEST", "Invalid status payload", status.HTTP_400_BAD_REQUEST)

        order = Order.objects.filter(id=order_id).prefetch_related("items").first()
        if not order:
            return _error("NOT_FOUND", "Order not found", status.HTTP_404_NOT_FOUND)

        new_status = serializer.validated_data["status"]
        if new_status not in ALLOWED_TRANSITIONS.get(order.status, set()):
            return _error("INVALID_STATE_TRANSITION", "Status transition is not allowed", status.HTTP_409_CONFLICT)

        order.status = new_status
        if new_status == Order.Status.CANCELED and serializer.validated_data.get("reason"):
            order.cancel_reason = serializer.validated_data.get("reason")
        order.save(update_fields=["status", "cancel_reason", "updated_at"])

        if new_status == Order.Status.DELIVERED:
            result, fulfill_error = _inventory_call(
                settings.B2B_FULFILL_URL,
                {"order_id": str(order.id), "items": [{"sku_id": str(item.sku_id), "quantity": item.quantity} for item in order.items.all()]},
            )
            if fulfill_error == "unavailable" or result[0] != status.HTTP_200_OK:
                _outbox_event(order.id, "ORDER_FULFILL_PENDING", {"order_id": str(order.id)})

        _outbox_event(order.id, "ORDER_STATUS_UPDATED", {"order_id": str(order.id), "status": order.status})
        return Response(OrderSerializer(order).data)
