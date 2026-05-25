from datetime import datetime, timezone
from uuid import UUID, uuid4

import jwt
from django.conf import settings
from django.db import transaction
from django.db.models import Count, F, Q, Sum
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, extend_schema_view
from jwt import InvalidTokenError
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import (
    Category,
    IntegrationInbox,
    IntegrationOutbox,
    InventoryOperation,
    Invoice,
    InvoiceItem,
    Product,
    SellerProfile,
    Sku,
)
from .serializers import (
    AcceptInvoiceRequestSerializer,
    CatalogProductSerializer,
    CreateInvoiceRequestSerializer,
    CreateProductRequestSerializer,
    CreateSkuRequestSerializer,
    DashboardOverviewSerializer,
    DashboardStatsSerializer,
    FulfillRequestSerializer,
    InvoiceSerializer,
    ModerationDecisionSerializer,
    ProductSerializer,
    ReserveRequestSerializer,
    SellerProfileSerializer,
    SellerProfileUpdateSerializer,
    SkuSerializer,
    UpdateProductRequestSerializer,
    UpdateSkuRequestSerializer,
)


def _format_error_message(message):
    if isinstance(message, str):
        return message
    if isinstance(message, dict):
        parts = []
        for field, errors in message.items():
            if isinstance(errors, list):
                for error in errors:
                    if field in (serializers.NON_FIELD_ERRORS, '__all__'):
                        parts.append(str(error))
                    else:
                        parts.append(f'{field}: {error}')
            else:
                parts.append(f'{field}: {errors}')
        return '; '.join(parts) if parts else 'Validation failed.'
    return str(message)


def _error(code, message, http_status):
    return Response({'code': code, 'message': _format_error_message(message)}, status=http_status)


def _parse_uuid(value):
    if not value:
        return None
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


def _decode_token(token):
    algorithm = settings.JWT_ALGORITHM
    key = settings.JWT_SECRET if algorithm.startswith('HS') else settings.JWT_PUBLIC_KEY
    if not key:
        return None

    decode_kwargs = {
        'algorithms': [algorithm],
        'options': {
            'verify_signature': True,
            'verify_exp': True,
            'verify_aud': bool(settings.JWT_AUDIENCE),
            'verify_iss': bool(settings.JWT_ISSUER),
        },
    }
    if settings.JWT_AUDIENCE:
        decode_kwargs['audience'] = settings.JWT_AUDIENCE
    if settings.JWT_ISSUER:
        decode_kwargs['issuer'] = settings.JWT_ISSUER

    try:
        return jwt.decode(token, key=key, **decode_kwargs)
    except InvalidTokenError:
        return None


def _looks_like_seller_request(request):
    return request.headers.get('Authorization', '').startswith('Bearer ')


def _get_seller_id(request):
    auth_header = request.headers.get('Authorization', '')
    if auth_header.startswith('Bearer '):
        token = auth_header.split(' ', 1)[1].strip()
        payload = _decode_token(token)
        if payload is None:
            return None, _error('UNAUTHORIZED', 'Invalid JWT token', status.HTTP_401_UNAUTHORIZED)

        seller_id = _parse_uuid(payload.get('sub') or payload.get('seller_id') or payload.get('user_id'))
        if seller_id:
            return seller_id, None
        return None, _error('UNAUTHORIZED', 'JWT does not contain seller id', status.HTTP_401_UNAUTHORIZED)

    return None, _error('UNAUTHORIZED', 'Seller identity is required', status.HTTP_401_UNAUTHORIZED)


def _expected_service_key():
    return getattr(settings, 'INTERNAL_SERVICE_KEY', 'neomarket-internal-key')


def _service_key_is_valid(request):
    provided = (request.headers.get('X-Service-Key') or '').strip()
    return bool(provided) and provided == _expected_service_key()


def _require_service_key(request):
    if _service_key_is_valid(request):
        return None
    return _error('UNAUTHORIZED', 'Missing or invalid X-Service-Key', status.HTTP_401_UNAUTHORIZED)


def _outbox_event(aggregate_id, event_type, payload):
    IntegrationOutbox.objects.create(
        aggregate_id=aggregate_id,
        event_type=event_type,
        payload=payload,
    )


def _get_or_create_profile(seller_id):
    return SellerProfile.objects.get_or_create(
        seller_id=seller_id,
        defaults={
            'company_name': 'NeoMarket Seller',
            'contact_person': 'Команда продаж',
        },
    )[0]


def _serialize_product_snapshot(product):
    fresh_product = (
        Product.objects
        .select_related('category')
        .prefetch_related('skus')
        .filter(id=product.id)
        .first()
    )
    return ProductSerializer(fresh_product or product).data


def _clear_blocking_state(product):
    product.blocking_reason = None
    product.field_reports = []


def _send_product_event(product, event_name, lifecycle_event, snapshot_before=None, extra=None):
    payload = {
        'product_id': str(product.id),
        'event_type': lifecycle_event,
        'idempotency_key': str(uuid4()),
        'snapshot_after': _serialize_product_snapshot(product),
    }
    if snapshot_before is not None:
        payload['snapshot_before'] = snapshot_before
    if extra:
        payload.update(extra)
    _outbox_event(product.id, event_name, payload)


def _send_sku_stock_event(sku, event_name='SKU_OUT_OF_STOCK'):
    _outbox_event(
        sku.product_id,
        event_name,
        {
            'event_type': event_name,
            'idempotency_key': str(uuid4()),
            'product_id': str(sku.product_id),
            'sku_id': str(sku.id),
        },
    )


def _set_product_on_moderation(product, snapshot_before=None, lifecycle_event='EDITED'):
    product.status = Product.Status.ON_MODERATION
    _clear_blocking_state(product)
    product.save(update_fields=['status', 'blocking_reason', 'field_reports', 'updated_at'])
    _send_product_event(product, 'PRODUCT_UPDATED', lifecycle_event, snapshot_before=snapshot_before)


def _normalize_field_reports(field_reports):
    normalized = []
    for item in field_reports or []:
        if isinstance(item, dict):
            field_name = str(item.get('field') or item.get('name') or '').strip()
            message = str(item.get('message') or item.get('comment') or item.get('title') or '').strip()
            if field_name or message:
                normalized.append(
                    {
                        'field': field_name,
                        'message': message or 'Требуется исправление после модерации',
                    }
                )
            continue

        text = str(item or '').strip()
        if text:
            normalized.append(
                {
                    'field': text,
                    'message': 'Требуется исправление после модерации',
                }
            )
    return normalized


def apply_moderation_decision(validated_data):
    event_key = validated_data['idempotency_key']
    existing = IntegrationInbox.objects.filter(message_id=event_key).first()
    if existing:
        return Product.objects.filter(id=validated_data['product_id']).select_related('category').prefetch_related('skus').first()

    product = Product.objects.select_related('category').prefetch_related('skus').filter(id=validated_data['product_id']).first()
    if not product:
        return None

    snapshot_before = _serialize_product_snapshot(product)
    decision_status = validated_data['status']
    field_reports = _normalize_field_reports(validated_data.get('field_reports', []))

    if decision_status == Product.Status.MODERATED:
        product.status = Product.Status.MODERATED
        _clear_blocking_state(product)
    else:
        product.status = Product.Status.HARD_BLOCKED if validated_data.get('hard_block') else Product.Status.BLOCKED
        product.blocking_reason = validated_data.get('blocking_reason') or {'title': 'Moderation blocked the product'}
        product.field_reports = field_reports

    product.save(update_fields=['status', 'blocking_reason', 'field_reports', 'updated_at'])
    IntegrationInbox.objects.create(
        message_id=event_key,
        source='moderation',
        event_type=f'MODERATION_{decision_status}',
        payload={
            **validated_data,
            'product_id': str(validated_data['product_id']),
            'field_reports': field_reports,
        },
    )
    _send_product_event(product, 'PRODUCT_UPDATED', 'UPDATED', snapshot_before=snapshot_before)

    if decision_status != Product.Status.MODERATED:
        _outbox_event(
            product.id,
            'PRODUCT_BLOCKED',
            {
                'product_id': str(product.id),
                'event_type': 'PRODUCT_BLOCKED',
                'idempotency_key': str(uuid4()),
                'sku_ids': [str(sku_id) for sku_id in product.skus.filter(deleted=False).values_list('id', flat=True)],
                'hard_block': product.status == Product.Status.HARD_BLOCKED,
            },
        )
    return product


def _inventory_operation_response(items, message):
    return {
        'items': items,
        'message': message,
    }


def _annotated_seller_products_queryset(seller_id):
    return (
        Product.objects.filter(seller_id=seller_id)
        .select_related('category')
        .prefetch_related('skus')
        .annotate(
            skus_count=Count('skus', filter=Q(skus__deleted=False), distinct=True),
            total_active_quantity=Sum('skus__active_quantity', filter=Q(skus__deleted=False)),
        )
    )


@extend_schema_view(
    get=extend_schema(operation_id='b2b_dashboard_overview', responses=DashboardOverviewSerializer),
)
class DashboardOverviewView(APIView):
    def get(self, request):
        seller_id, error = _get_seller_id(request)
        if error:
            return error

        products = Product.objects.filter(seller_id=seller_id)
        skus = Sku.objects.filter(product__seller_id=seller_id, deleted=False)
        invoices = Invoice.objects.filter(seller_id=seller_id)

        overview = {
            'total_products': products.count(),
            'total_skus': skus.count(),
            'total_stock': skus.aggregate(total=Sum('active_quantity'))['total'] or 0,
            'created_products': products.filter(status=Product.Status.CREATED, deleted=False).count(),
            'on_moderation_products': products.filter(status=Product.Status.ON_MODERATION, deleted=False).count(),
            'blocked_products': products.filter(status__in=[Product.Status.BLOCKED, Product.Status.HARD_BLOCKED], deleted=False).count(),
            'pending_invoices': invoices.filter(status=Invoice.Status.CREATED).count(),
            'accepted_invoices': invoices.filter(status=Invoice.Status.ACCEPTED).count(),
        }
        return Response(overview)


@extend_schema_view(
    get=extend_schema(operation_id='b2b_dashboard_stats', responses=DashboardStatsSerializer),
)
class DashboardStatsView(APIView):
    def get(self, request):
        seller_id, error = _get_seller_id(request)
        if error:
            return error

        products = _annotated_seller_products_queryset(seller_id)
        low_stock_skus = (
            Sku.objects.select_related('product')
            .filter(product__seller_id=seller_id, active_quantity__lte=5, deleted=False)
            .order_by('active_quantity', 'name')[:8]
        )
        recent_products = products.order_by('-created_at')[:5]
        recent_invoices = Invoice.objects.filter(seller_id=seller_id).prefetch_related('items__sku').order_by('-created_at')[:5]
        status_rows = products.values('status').annotate(value=Count('id')).order_by('status')

        return Response(
            {
                'product_statuses': [{'label': row['status'], 'value': row['value']} for row in status_rows],
                'low_stock_skus': SkuSerializer(low_stock_skus, many=True).data,
                'recent_products': ProductSerializer(recent_products, many=True).data,
                'recent_invoices': InvoiceSerializer(recent_invoices, many=True).data,
            }
        )


@extend_schema_view(
    get=extend_schema(operation_id='b2b_get_profile', responses=SellerProfileSerializer),
    patch=extend_schema(operation_id='b2b_update_profile', request=SellerProfileUpdateSerializer, responses=SellerProfileSerializer),
)
class SellerProfileView(APIView):
    def get(self, request):
        seller_id, error = _get_seller_id(request)
        if error:
            return error

        profile = _get_or_create_profile(seller_id)
        return Response(SellerProfileSerializer(profile).data)

    @transaction.atomic
    def patch(self, request):
        seller_id, error = _get_seller_id(request)
        if error:
            return error

        serializer = SellerProfileUpdateSerializer(data=request.data)
        if not serializer.is_valid():
            return _error('BAD_REQUEST', 'Invalid profile payload', status.HTTP_400_BAD_REQUEST)

        profile = _get_or_create_profile(seller_id)
        for field, value in serializer.validated_data.items():
            setattr(profile, field, value)
        profile.save()
        return Response(SellerProfileSerializer(profile).data)


@extend_schema_view(
    get=extend_schema(operation_id='b2b_list_products', responses=OpenApiTypes.OBJECT),
    post=extend_schema(operation_id='b2b_create_product', request=CreateProductRequestSerializer, responses=ProductSerializer),
)
class ProductsView(APIView):
    def get(self, request):
        if not _looks_like_seller_request(request):
            error = _require_service_key(request)
            if error:
                return error
            return self._catalog_view(request)

        seller_id, error = _get_seller_id(request)
        if error:
            return error

        try:
            limit = max(1, min(int(request.query_params.get('limit', 20)), 100))
            offset = max(0, int(request.query_params.get('offset', 0)))
        except ValueError:
            return _error('BAD_REQUEST', 'Invalid pagination params', status.HTTP_400_BAD_REQUEST)

        queryset = _annotated_seller_products_queryset(seller_id)

        category_id = request.query_params.get('category_id')
        if category_id:
            queryset = queryset.filter(category_id=category_id)

        product_status = request.query_params.get('status')
        if product_status:
            queryset = queryset.filter(status=product_status)

        search = request.query_params.get('search')
        if search:
            queryset = queryset.filter(Q(title__icontains=search) | Q(description__icontains=search))

        total = queryset.count()
        items = queryset[offset : offset + limit]

        return Response(
            {
                'items': ProductSerializer(items, many=True).data,
                'total_count': total,
                'total': total,
                'limit': limit,
                'offset': offset,
            }
        )

    def _catalog_view(self, request):
        try:
            limit = max(1, min(int(request.query_params.get('limit', 20)), 100))
            offset = max(0, int(request.query_params.get('offset', 0)))
        except ValueError:
            return _error('BAD_REQUEST', 'Invalid pagination params', status.HTTP_400_BAD_REQUEST)

        queryset = (
            Product.objects.filter(
                status=Product.Status.MODERATED,
                deleted=False,
                skus__deleted=False,
                skus__active_quantity__gt=0,
            )
            .select_related('category')
            .prefetch_related('skus')
            .distinct()
        )

        ids_param = request.query_params.get('ids')
        if ids_param:
            raw_ids = [item.strip() for item in ids_param.split(',') if item.strip()]
            parsed_ids = [_parse_uuid(item) for item in raw_ids]
            if any(item is None for item in parsed_ids):
                return _error('BAD_REQUEST', 'Invalid ids filter', status.HTTP_400_BAD_REQUEST)
            queryset = queryset.filter(id__in=parsed_ids)

        total = queryset.count()
        items = queryset[offset : offset + limit]

        return Response(
            {
                'items': CatalogProductSerializer(items, many=True).data,
                'total_count': total,
                'total': total,
                'limit': limit,
                'offset': offset,
            }
        )

    @transaction.atomic
    def post(self, request):
        seller_id, error = _get_seller_id(request)
        if error:
            return error

        serializer = CreateProductRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return _error('BAD_REQUEST', serializer.errors, status.HTTP_400_BAD_REQUEST)

        category = Category.objects.get(id=serializer.validated_data['category_id'])
        product = Product.objects.create(
            seller_id=seller_id,
            title=serializer.validated_data['title'],
            description=serializer.validated_data['description'],
            category=category,
            images=serializer.validated_data.get('images', []),
            characteristics=serializer.validated_data.get('characteristics', []),
        )
        slug = serializer.validated_data.get('slug')
        if slug:
            product._api_slug = slug
        _send_product_event(product, 'PRODUCT_CREATED', 'CREATED')
        return Response(ProductSerializer(product).data, status=status.HTTP_201_CREATED)


def _product_id_from_kwargs(kwargs):
    return kwargs.get('product_id') or kwargs.get('id')


@extend_schema_view(
    get=extend_schema(operation_id='b2b_get_product', responses=ProductSerializer),
    put=extend_schema(operation_id='b2b_update_product', request=UpdateProductRequestSerializer, responses=ProductSerializer),
    patch=extend_schema(operation_id='b2b_patch_product', request=UpdateProductRequestSerializer, responses=ProductSerializer),
    delete=extend_schema(operation_id='b2b_delete_product', responses=None),
)
class ProductDetailView(APIView):
    def get(self, request, **kwargs):
        product_id = _product_id_from_kwargs(kwargs)
        if _service_key_is_valid(request):
            product = (
                Product.objects.filter(
                    id=product_id,
                    status=Product.Status.MODERATED,
                    deleted=False,
                )
                .select_related('category')
                .prefetch_related('skus')
                .first()
            )
            if not product:
                return _error('NOT_FOUND', 'Product not found', status.HTTP_404_NOT_FOUND)
            return Response(CatalogProductSerializer(product).data)

        seller_id, error = _get_seller_id(request)
        if error:
            return error

        product = Product.objects.filter(id=product_id, seller_id=seller_id).select_related('category').prefetch_related('skus').first()
        if not product:
            return _error('NOT_FOUND', 'Product not found', status.HTTP_404_NOT_FOUND)

        return Response(ProductSerializer(product).data)

    @transaction.atomic
    def put(self, request, **kwargs):
        return self._update_product(request, **kwargs)

    @transaction.atomic
    def patch(self, request, **kwargs):
        return self._update_product(request, **kwargs)

    @transaction.atomic
    def _update_product(self, request, **kwargs):
        product_id = _product_id_from_kwargs(kwargs)
        seller_id, error = _get_seller_id(request)
        if error:
            return error

        serializer = UpdateProductRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return _error('BAD_REQUEST', serializer.errors, status.HTTP_400_BAD_REQUEST)

        product = Product.objects.select_related('category').prefetch_related('skus').filter(id=product_id).first()
        if not product:
            return _error('NOT_FOUND', 'Product not found', status.HTTP_404_NOT_FOUND)
        if product.seller_id != seller_id:
            return _error('FORBIDDEN', 'Cannot update another seller product', status.HTTP_403_FORBIDDEN)
        if product.deleted:
            return _error('BAD_REQUEST', 'Deleted product cannot be edited', status.HTTP_400_BAD_REQUEST)
        if product.status == Product.Status.HARD_BLOCKED:
            return _error('FORBIDDEN', 'HARD_BLOCKED product cannot be edited', status.HTTP_403_FORBIDDEN)

        snapshot_before = _serialize_product_snapshot(product)
        previous_status = product.status

        if 'category_id' in serializer.validated_data:
            category = Category.objects.filter(id=serializer.validated_data['category_id']).first()
            if category is None:
                return _error('BAD_REQUEST', 'Unknown category_id.', status.HTTP_400_BAD_REQUEST)
            product.category = category

        for field in ['title', 'description', 'images', 'characteristics']:
            if field in serializer.validated_data:
                setattr(product, field, serializer.validated_data[field])

        product.save()

        if previous_status in {Product.Status.MODERATED, Product.Status.BLOCKED}:
            _set_product_on_moderation(product, snapshot_before=snapshot_before, lifecycle_event='EDITED')
        else:
            _send_product_event(product, 'PRODUCT_UPDATED', 'EDITED', snapshot_before=snapshot_before)

        return Response(ProductSerializer(product).data)

    @transaction.atomic
    def delete(self, request, **kwargs):
        product_id = _product_id_from_kwargs(kwargs)
        seller_id, error = _get_seller_id(request)
        if error:
            return error

        product = Product.objects.prefetch_related('skus').filter(id=product_id).first()
        if not product:
            return _error('NOT_FOUND', 'Product not found', status.HTTP_404_NOT_FOUND)
        if product.seller_id != seller_id:
            return _error('FORBIDDEN', 'Cannot delete another seller product', status.HTTP_403_FORBIDDEN)
        if product.status == Product.Status.HARD_BLOCKED:
            return _error('FORBIDDEN', 'HARD_BLOCKED product cannot be deleted', status.HTTP_403_FORBIDDEN)
        if product.deleted:
            return _error('BAD_REQUEST', 'Product is already deleted', status.HTTP_400_BAD_REQUEST)
        if product.skus.filter(deleted=False, reserved_quantity__gt=0).exists():
            return _error('CONFLICT', 'Product has SKU with active reserves', status.HTTP_409_CONFLICT)

        snapshot_before = _serialize_product_snapshot(product)
        sku_ids = [str(sku_id) for sku_id in product.skus.filter(deleted=False).values_list('id', flat=True)]
        product.skus.filter(deleted=False).update(deleted=True, active_quantity=0, updated_at=datetime.now(timezone.utc))
        product.deleted = True
        product.save(update_fields=['deleted', 'updated_at'])

        _outbox_event(
            product.id,
            'PRODUCT_UPDATED',
            {
                'product_id': str(product.id),
                'event_type': 'DELETED',
                'idempotency_key': str(uuid4()),
                'snapshot_before': snapshot_before,
                'snapshot_after': _serialize_product_snapshot(product),
            },
        )
        _outbox_event(
            product.id,
            'PRODUCT_DELETED',
            {
                'product_id': str(product.id),
                'event_type': 'DELETED',
                'idempotency_key': str(uuid4()),
                'sku_ids': sku_ids,
                'snapshot_before': snapshot_before,
            },
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class SkuListByProductView(APIView):
    def get(self, request, product_id):
        seller_id, error = _get_seller_id(request)
        if error:
            return error

        product = Product.objects.filter(id=product_id, seller_id=seller_id).prefetch_related('skus').first()
        if not product:
            return _error('NOT_FOUND', 'Product not found', status.HTTP_404_NOT_FOUND)

        skus = [sku for sku in product.skus.all() if not sku.deleted]
        return Response(SkuSerializer(skus, many=True).data)


@extend_schema_view(
    get=extend_schema(operation_id='b2b_get_sku', responses=SkuSerializer),
    put=extend_schema(operation_id='b2b_update_sku', request=UpdateSkuRequestSerializer, responses=SkuSerializer),
    patch=extend_schema(operation_id='b2b_patch_sku', request=UpdateSkuRequestSerializer, responses=SkuSerializer),
    delete=extend_schema(operation_id='b2b_delete_sku', responses=None),
)
class SkuDetailView(APIView):
    def get(self, request, **kwargs):
        sku_id = kwargs.get('sku_id') or kwargs.get('id')
        seller_id, error = _get_seller_id(request)
        if error:
            return error

        sku = Sku.objects.select_related('product').filter(id=sku_id, product__seller_id=seller_id, deleted=False).first()
        if not sku:
            return _error('NOT_FOUND', 'SKU not found', status.HTTP_404_NOT_FOUND)
        return Response(SkuSerializer(sku).data)

    @transaction.atomic
    def put(self, request, **kwargs):
        return self._update_sku(request, **kwargs)

    @transaction.atomic
    def patch(self, request, **kwargs):
        return self._update_sku(request, **kwargs)

    @transaction.atomic
    def _update_sku(self, request, **kwargs):
        sku_id = kwargs.get('sku_id') or kwargs.get('id')
        seller_id, error = _get_seller_id(request)
        if error:
            return error

        serializer = UpdateSkuRequestSerializer(data={**request.data, 'id': str(sku_id)})
        if not serializer.is_valid():
            return _error('BAD_REQUEST', serializer.errors, status.HTTP_400_BAD_REQUEST)

        sku = Sku.objects.select_related('product').prefetch_related('product__skus').filter(id=sku_id).first()
        if not sku or sku.deleted:
            return _error('NOT_FOUND', 'SKU not found', status.HTTP_404_NOT_FOUND)
        if sku.product.seller_id != seller_id:
            return _error('FORBIDDEN', 'Cannot update another seller SKU', status.HTTP_403_FORBIDDEN)
        if sku.product.status == Product.Status.HARD_BLOCKED:
            return _error('FORBIDDEN', 'HARD_BLOCKED product cannot be edited', status.HTTP_403_FORBIDDEN)

        snapshot_before = _serialize_product_snapshot(sku.product)
        previous_status = sku.product.status

        for field in ['name', 'price', 'cost_price', 'active_quantity', 'images', 'characteristics']:
            if field in serializer.validated_data:
                setattr(sku, field, serializer.validated_data[field])

        sku.save()

        if previous_status in {Product.Status.MODERATED, Product.Status.BLOCKED}:
            _set_product_on_moderation(sku.product, snapshot_before=snapshot_before, lifecycle_event='EDITED')
        else:
            _send_product_event(sku.product, 'PRODUCT_UPDATED', 'EDITED', snapshot_before=snapshot_before)

        return Response(SkuSerializer(sku).data)

    @transaction.atomic
    def delete(self, request, **kwargs):
        sku_id = kwargs.get('sku_id') or kwargs.get('id')
        return SkuMutationView().delete(request, id=sku_id)


@extend_schema_view(
    post=extend_schema(operation_id='b2b_create_sku', request=CreateSkuRequestSerializer, responses=SkuSerializer),
)
class SkuMutationView(APIView):
    @transaction.atomic
    def post(self, request):
        seller_id, error = _get_seller_id(request)
        if error:
            return error

        serializer = CreateSkuRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return _error('BAD_REQUEST', serializer.errors, status.HTTP_400_BAD_REQUEST)

        product = Product.objects.select_related('category').prefetch_related('skus').filter(id=serializer.validated_data['product_id']).first()
        if not product:
            return _error('NOT_FOUND', 'Product not found', status.HTTP_404_NOT_FOUND)
        if product.seller_id != seller_id:
            return _error('FORBIDDEN', 'Cannot add SKU to another seller product', status.HTTP_403_FORBIDDEN)
        if product.deleted:
            return _error('BAD_REQUEST', 'Cannot add SKU to deleted product', status.HTTP_400_BAD_REQUEST)
        if product.status == Product.Status.HARD_BLOCKED:
            return _error('FORBIDDEN', 'Cannot add SKU to HARD_BLOCKED product', status.HTTP_403_FORBIDDEN)

        first_live_sku = not product.skus.filter(deleted=False).exists()
        snapshot_before = _serialize_product_snapshot(product)

        sku = Sku.objects.create(
            product=product,
            name=serializer.validated_data['name'],
            price=serializer.validated_data['price'],
            cost_price=serializer.validated_data.get('cost_price', 0),
            active_quantity=serializer.validated_data['active_quantity'],
            images=serializer.validated_data.get('images', []),
            characteristics=serializer.validated_data.get('characteristics', []),
        )

        if first_live_sku:
            _set_product_on_moderation(product, snapshot_before=snapshot_before, lifecycle_event='CREATED')

        return Response(SkuSerializer(sku).data, status=status.HTTP_201_CREATED)

    @transaction.atomic
    def delete(self, request, id=None, sku_id=None):
        seller_id, error = _get_seller_id(request)
        if error:
            return error

        parsed_id = sku_id or id or _parse_uuid(request.query_params.get('id'))
        if not parsed_id:
            return _error('BAD_REQUEST', 'SKU id is required', status.HTTP_400_BAD_REQUEST)

        sku = Sku.objects.select_related('product').prefetch_related('product__skus').filter(id=parsed_id).first()
        if not sku or sku.deleted:
            return _error('NOT_FOUND', 'SKU not found', status.HTTP_404_NOT_FOUND)
        if sku.product.seller_id != seller_id:
            return _error('FORBIDDEN', 'Cannot delete another seller SKU', status.HTTP_403_FORBIDDEN)
        if sku.product.status == Product.Status.HARD_BLOCKED:
            return _error('FORBIDDEN', 'HARD_BLOCKED product cannot be edited', status.HTTP_403_FORBIDDEN)
        if int(sku.reserved_quantity or 0) > 0:
            return _error('CONFLICT', 'SKU has active reserves', status.HTTP_409_CONFLICT)

        snapshot_before = _serialize_product_snapshot(sku.product)
        had_stock = sku.product.status == Product.Status.MODERATED and int(sku.active_quantity or 0) > 0

        sku.deleted = True
        sku.active_quantity = 0
        sku.save(update_fields=['deleted', 'active_quantity', 'updated_at'])

        if had_stock:
            _send_sku_stock_event(sku)

        has_other_live_skus = sku.product.skus.filter(deleted=False).exclude(id=sku.id).exists()
        if not has_other_live_skus and sku.product.status == Product.Status.ON_MODERATION:
            sku.product.status = Product.Status.CREATED
            sku.product.save(update_fields=['status', 'updated_at'])
            _send_product_event(sku.product, 'PRODUCT_UPDATED', 'DELETED', snapshot_before=snapshot_before)

        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema_view(
    get=extend_schema(operation_id='b2b_list_invoices', responses=OpenApiTypes.OBJECT),
    post=extend_schema(operation_id='b2b_create_invoice', request=CreateInvoiceRequestSerializer, responses=InvoiceSerializer),
)
class InvoicesView(APIView):
    def get(self, request):
        seller_id, error = _get_seller_id(request)
        if error:
            return error

        invoices = Invoice.objects.filter(seller_id=seller_id).prefetch_related('items__sku')
        total = invoices.count()
        return Response({'items': InvoiceSerializer(invoices, many=True).data, 'total_count': total, 'total': total})

    @transaction.atomic
    def post(self, request):
        seller_id, error = _get_seller_id(request)
        if error:
            return error

        serializer = CreateInvoiceRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return _error('BAD_REQUEST', serializer.errors, status.HTTP_400_BAD_REQUEST)

        if serializer.validated_data.get('seller_id') and serializer.validated_data['seller_id'] != seller_id:
            return _error('FORBIDDEN', 'seller_id in payload must match authenticated seller', status.HTTP_403_FORBIDDEN)

        sku_ids = [row['sku_id'] for row in serializer.validated_data['items']]
        skus = {
            sku.id: sku
            for sku in Sku.objects.select_related('product').filter(id__in=sku_ids, deleted=False)
        }

        rows = []
        for item in serializer.validated_data['items']:
            sku = skus.get(item['sku_id'])
            if not sku:
                return _error('BAD_REQUEST', 'Invoice contains unknown sku', status.HTTP_400_BAD_REQUEST)
            if sku.product.seller_id != seller_id:
                return _error('FORBIDDEN', 'Invoice contains foreign sku', status.HTTP_403_FORBIDDEN)
            if sku.product.deleted or sku.product.status != Product.Status.MODERATED:
                return _error('BAD_REQUEST', 'Invoice accepts only MODERATED seller skus', status.HTTP_400_BAD_REQUEST)
            rows.append((sku, item['quantity']))

        invoice = Invoice.objects.create(
            seller_id=seller_id,
            warehouse_id=serializer.validated_data['warehouse_id'],
            status=Invoice.Status.CREATED,
        )
        InvoiceItem.objects.bulk_create([InvoiceItem(invoice=invoice, sku=sku, quantity=quantity) for sku, quantity in rows])
        invoice.refresh_from_db()
        return Response(InvoiceSerializer(invoice).data, status=status.HTTP_201_CREATED)


class InvoiceDetailView(APIView):
    def get(self, request, invoice_id):
        seller_id, error = _get_seller_id(request)
        if error:
            return error

        invoice = Invoice.objects.filter(id=invoice_id, seller_id=seller_id).prefetch_related('items__sku').first()
        if not invoice:
            return _error('NOT_FOUND', 'Invoice not found', status.HTTP_404_NOT_FOUND)
        return Response(InvoiceSerializer(invoice).data)


@extend_schema_view(
    post=extend_schema(operation_id='b2b_accept_invoice', request=AcceptInvoiceRequestSerializer, responses=InvoiceSerializer),
)
class InvoiceAcceptView(APIView):
    @transaction.atomic
    def post(self, request, invoice_id=None):
        seller_id, error = _get_seller_id(request)
        if error:
            return error

        payload = dict(request.data or {})
        if invoice_id:
            payload['invoice_id'] = str(invoice_id)
        serializer = AcceptInvoiceRequestSerializer(data=payload)
        if not serializer.is_valid():
            return _error('BAD_REQUEST', serializer.errors, status.HTTP_400_BAD_REQUEST)

        invoice = Invoice.objects.select_for_update().filter(id=serializer.validated_data['invoice_id'], seller_id=seller_id).first()
        if not invoice:
            return _error('NOT_FOUND', 'Invoice not found', status.HTTP_404_NOT_FOUND)

        if invoice.status != Invoice.Status.CREATED:
            return _error('BAD_REQUEST', 'Only CREATED invoice can be accepted', status.HTTP_400_BAD_REQUEST)

        items = list(invoice.items.select_related('sku'))
        for item in items:
            Sku.objects.filter(id=item.sku_id).update(active_quantity=F('active_quantity') + item.quantity)

        invoice.status = Invoice.Status.ACCEPTED
        invoice.accepted_at = datetime.now(timezone.utc)
        invoice.save(update_fields=['status', 'accepted_at'])

        _outbox_event(
            invoice.id,
            'INVOICE_ACCEPTED',
            {'invoice_id': str(invoice.id), 'seller_id': str(invoice.seller_id), 'event_type': 'ACCEPTED'},
        )
        return Response(InvoiceSerializer(invoice).data)


class ReserveView(APIView):
    @transaction.atomic
    def post(self, request):
        error = _require_service_key(request)
        if error:
            return error

        serializer = ReserveRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return _error('BAD_REQUEST', serializer.errors, status.HTTP_400_BAD_REQUEST)

        key = serializer.validated_data['idempotency_key']
        existing = InventoryOperation.objects.filter(key=key).first()
        if existing:
            if existing.kind != InventoryOperation.Kind.RESERVE:
                return _error('CONFLICT', 'Idempotency key already used by another operation', status.HTTP_409_CONFLICT)
            return Response(existing.payload)

        requested_items = serializer.validated_data['items']
        sku_ids = [row['sku_id'] for row in requested_items]
        sku_map = {
            sku.id: sku
            for sku in Sku.objects.select_for_update().select_related('product').filter(id__in=sku_ids, deleted=False).order_by('id')
        }
        if len(sku_map) != len(set(sku_ids)):
            return _error('BAD_REQUEST', 'One or more SKU ids are invalid', status.HTTP_400_BAD_REQUEST)

        response_items = []
        for item in requested_items:
            sku = sku_map[item['sku_id']]
            if sku.product.deleted or sku.product.status != Product.Status.MODERATED:
                return _error('BAD_REQUEST', 'Only MODERATED SKUs can be reserved', status.HTTP_400_BAD_REQUEST)
            if int(sku.active_quantity or 0) < item['quantity']:
                return _error('CONFLICT', 'Insufficient stock for reserve', status.HTTP_409_CONFLICT)

        for item in requested_items:
            sku = sku_map[item['sku_id']]
            sku.active_quantity -= item['quantity']
            sku.reserved_quantity += item['quantity']
            sku.save(update_fields=['active_quantity', 'reserved_quantity', 'updated_at'])
            if sku.active_quantity == 0:
                _send_sku_stock_event(sku)
            response_items.append(
                {
                    'sku_id': str(sku.id),
                    'active_quantity': sku.active_quantity,
                    'reserved_quantity': sku.reserved_quantity,
                }
            )

        payload = _inventory_operation_response(response_items, 'Inventory reserved')
        InventoryOperation.objects.create(key=key, kind=InventoryOperation.Kind.RESERVE, payload=payload)
        return Response(payload)


class UnreserveView(APIView):
    @transaction.atomic
    def post(self, request):
        error = _require_service_key(request)
        if error:
            return error

        serializer = ReserveRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return _error('BAD_REQUEST', serializer.errors, status.HTTP_400_BAD_REQUEST)

        key = serializer.validated_data['idempotency_key']
        existing = InventoryOperation.objects.filter(key=key).first()
        if existing:
            if existing.kind != InventoryOperation.Kind.UNRESERVE:
                return _error('CONFLICT', 'Idempotency key already used by another operation', status.HTTP_409_CONFLICT)
            return Response(existing.payload)

        requested_items = serializer.validated_data['items']
        sku_ids = [row['sku_id'] for row in requested_items]
        sku_map = {
            sku.id: sku
            for sku in Sku.objects.select_for_update().select_related('product').filter(id__in=sku_ids, deleted=False).order_by('id')
        }
        if len(sku_map) != len(set(sku_ids)):
            return _error('BAD_REQUEST', 'One or more SKU ids are invalid', status.HTTP_400_BAD_REQUEST)

        response_items = []
        for item in requested_items:
            sku = sku_map[item['sku_id']]
            if int(sku.reserved_quantity or 0) < item['quantity']:
                return _error('CONFLICT', 'Cannot unreserve more than reserved quantity', status.HTTP_409_CONFLICT)

        for item in requested_items:
            sku = sku_map[item['sku_id']]
            sku.active_quantity += item['quantity']
            sku.reserved_quantity -= item['quantity']
            sku.save(update_fields=['active_quantity', 'reserved_quantity', 'updated_at'])
            response_items.append(
                {
                    'sku_id': str(sku.id),
                    'active_quantity': sku.active_quantity,
                    'reserved_quantity': sku.reserved_quantity,
                }
            )

        payload = _inventory_operation_response(response_items, 'Inventory unreserved')
        InventoryOperation.objects.create(key=key, kind=InventoryOperation.Kind.UNRESERVE, payload=payload)
        return Response(payload)


class FulfillView(APIView):
    @transaction.atomic
    def post(self, request):
        error = _require_service_key(request)
        if error:
            return error

        serializer = FulfillRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return _error('BAD_REQUEST', serializer.errors, status.HTTP_400_BAD_REQUEST)

        key = f'FULFILL:{serializer.validated_data["order_id"]}'
        existing = InventoryOperation.objects.filter(key=key).first()
        if existing:
            if existing.kind != InventoryOperation.Kind.FULFILL:
                return _error('CONFLICT', 'Order already processed by another operation', status.HTTP_409_CONFLICT)
            return Response(existing.payload)

        requested_items = serializer.validated_data['items']
        sku_ids = [row['sku_id'] for row in requested_items]
        sku_map = {
            sku.id: sku
            for sku in Sku.objects.select_for_update().select_related('product').filter(id__in=sku_ids, deleted=False).order_by('id')
        }
        if len(sku_map) != len(set(sku_ids)):
            return _error('BAD_REQUEST', 'One or more SKU ids are invalid', status.HTTP_400_BAD_REQUEST)

        response_items = []
        for item in requested_items:
            sku = sku_map[item['sku_id']]
            if int(sku.reserved_quantity or 0) < item['quantity']:
                return _error('CONFLICT', 'Cannot fulfill more than reserved quantity', status.HTTP_409_CONFLICT)

        for item in requested_items:
            sku = sku_map[item['sku_id']]
            sku.reserved_quantity -= item['quantity']
            sku.save(update_fields=['reserved_quantity', 'updated_at'])
            response_items.append(
                {
                    'sku_id': str(sku.id),
                    'active_quantity': sku.active_quantity,
                    'reserved_quantity': sku.reserved_quantity,
                }
            )

        payload = _inventory_operation_response(response_items, 'Fulfill applied')
        InventoryOperation.objects.create(key=key, kind=InventoryOperation.Kind.FULFILL, payload=payload)
        return Response(payload)


class ModerationEventsView(APIView):
    @transaction.atomic
    def post(self, request):
        error = _require_service_key(request)
        if error:
            return error

        serializer = ModerationDecisionSerializer(data=request.data)
        if not serializer.is_valid():
            return _error('BAD_REQUEST', serializer.errors, status.HTTP_400_BAD_REQUEST)

        product = apply_moderation_decision(serializer.validated_data)
        if not product:
            return _error('NOT_FOUND', 'Product not found', status.HTTP_404_NOT_FOUND)
        return Response(ProductSerializer(product).data)


class PublicProductListView(APIView):
    def get(self, request):
        error = _require_service_key(request)
        if error:
            return error
        return ProductsView()._catalog_view(request)


class PublicProductBatchView(APIView):
    def post(self, request):
        error = _require_service_key(request)
        if error:
            return error
        ids = request.data.get('ids') or []
        if not isinstance(ids, list) or not ids:
            return _error('BAD_REQUEST', 'ids must be a non-empty array', status.HTTP_400_BAD_REQUEST)
        query = request._request.GET.copy()
        query['ids'] = ','.join(str(item) for item in ids)
        request._request.GET = query
        return ProductsView()._catalog_view(request)


class PublicProductDetailView(APIView):
    def get(self, request, product_id):
        error = _require_service_key(request)
        if error:
            return error
        product = (
            Product.objects.filter(id=product_id, status=Product.Status.MODERATED, deleted=False)
            .select_related('category')
            .prefetch_related('skus')
            .first()
        )
        if not product:
            return _error('NOT_FOUND', 'Product not found', status.HTTP_404_NOT_FOUND)
        return Response(CatalogProductSerializer(product).data)


class PublicProductSimilarView(APIView):
    def get(self, request, product_id):
        error = _require_service_key(request)
        if error:
            return error
        product = Product.objects.filter(id=product_id, deleted=False).select_related('category').first()
        if not product:
            return _error('NOT_FOUND', 'Product not found', status.HTTP_404_NOT_FOUND)

        queryset = (
            Product.objects.filter(status=Product.Status.MODERATED, deleted=False, category_id=product.category_id)
            .exclude(id=product.id)
            .select_related('category')
            .prefetch_related('skus')[:12]
        )
        return Response({'items': CatalogProductSerializer(queryset, many=True).data})


class PublicSkuDetailView(APIView):
    def get(self, request, sku_id):
        error = _require_service_key(request)
        if error:
            return error
        sku = (
            Sku.objects.select_related('product', 'product__category')
            .filter(id=sku_id, deleted=False, product__status=Product.Status.MODERATED, product__deleted=False)
            .first()
        )
        if not sku:
            return _error('NOT_FOUND', 'SKU not found', status.HTTP_404_NOT_FOUND)
        return Response(CatalogSkuSerializer(sku).data)
