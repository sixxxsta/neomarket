from datetime import datetime, timezone
from uuid import uuid4

from django.db import transaction
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, extend_schema_view
from jwt import InvalidTokenError
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .auth import authenticate_request, has_any_role
from .models import BlockingReason, ModerationCard, ModerationEvent
from .product_events import apply_product_event
from .serializers import (
    BlockingReasonSerializer,
    DeclineRequestSerializer,
    EnqueueRequestSerializer,
    ModerationCardSerializer,
)


def _error(message, code, http_status):
    return Response({'code': code, 'message': message}, status=http_status)


def _authorize_moderator(request):
    try:
        context = authenticate_request(request)
    except InvalidTokenError as exc:
        return None, _error(str(exc), 'UNAUTHORIZED', status.HTTP_401_UNAUTHORIZED)

    if not has_any_role(context, {'ADMIN', 'MODERATOR'}):
        return None, _error('Moderator or Admin role is required', 'FORBIDDEN', status.HTTP_403_FORBIDDEN)
    return context, None


def _build_field_reports(fields, message):
    reports = []
    for field in fields or []:
        field_name = str(field or '').strip()
        if not field_name:
            continue
        reports.append(
            {
                'field': field_name,
                'message': message or 'Требуется исправление после модерации',
            }
        )
    return reports


def _open_cards_queryset(product_id):
    return ModerationCard.objects.select_for_update().filter(
        product_id=product_id,
        queue_status__in=[ModerationCard.QueueStatus.PENDING, ModerationCard.QueueStatus.IN_REVIEW],
    ).order_by('created_at', 'id')


@extend_schema_view(
    post=extend_schema(operation_id='moderation_get_next_card', responses=OpenApiTypes.OBJECT),
)
class ModerationNextCardView(APIView):
    serializer_class = ModerationCardSerializer

    @transaction.atomic
    def post(self, request):
        auth_context, error = _authorize_moderator(request)
        if error:
            return error

        moderator = auth_context.actor

        # Та же сессия модератора после обновления страницы: вернуть уже взятый IN_REVIEW,
        # иначе get-next смотрит только на PENDING и очередь выглядит «пустой».
        existing = (
            ModerationCard.objects.select_for_update(skip_locked=True)
            .filter(
                queue_status=ModerationCard.QueueStatus.IN_REVIEW,
                assigned_to=moderator,
            )
            .order_by('updated_at')
            .first()
        )
        if existing:
            return Response(ModerationCardSerializer(existing).data)

        card = (
            ModerationCard.objects.select_for_update(skip_locked=True)
            .filter(queue_status=ModerationCard.QueueStatus.PENDING)
            .order_by('created_at')
            .first()
        )
        if not card:
            return Response(status=status.HTTP_204_NO_CONTENT)

        card.queue_status = ModerationCard.QueueStatus.IN_REVIEW
        card.assigned_to = moderator
        card.save(update_fields=['queue_status', 'assigned_to', 'updated_at'])

        return Response(ModerationCardSerializer(card).data)


@extend_schema_view(
    post=extend_schema(
        operation_id='moderation_enqueue_product',
        request=EnqueueRequestSerializer,
        responses=ModerationCardSerializer,
    ),
)
class ModerationEnqueueView(APIView):
    serializer_class = EnqueueRequestSerializer

    @transaction.atomic
    def post(self, request):
        _auth_context, error = _authorize_moderator(request)
        if error:
            return error

        serializer = EnqueueRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return _error('Invalid enqueue payload', 'BAD_REQUEST', status.HTTP_400_BAD_REQUEST)

        card = ModerationCard.objects.create(
            product_id=serializer.validated_data['product_id'],
            event_type=serializer.validated_data['event_type'],
            snapshot_before=serializer.validated_data.get('snapshot_before'),
            snapshot_after=serializer.validated_data.get('snapshot_after') or {'id': str(serializer.validated_data['product_id'])},
        )
        return Response(ModerationCardSerializer(card).data, status=status.HTTP_201_CREATED)


@extend_schema_view(
    post=extend_schema(operation_id='moderation_approve_product', responses=OpenApiTypes.OBJECT),
)
class ProductApproveView(APIView):
    serializer_class = ModerationCardSerializer

    @transaction.atomic
    def post(self, request, id):
        auth_context, error = _authorize_moderator(request)
        if error:
            return error

        moderator = auth_context.actor

        open_cards = _open_cards_queryset(id)
        if not open_cards.exists():
            return _error('Product is not found in moderation queue', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

        decided_at = datetime.now(timezone.utc)
        open_cards.update(
            queue_status=ModerationCard.QueueStatus.APPROVED,
            decided_by=moderator,
            decided_at=decided_at,
            updated_at=decided_at,
        )

        idempotency_key = str(uuid4())
        ModerationEvent.objects.create(
            event_type=ModerationEvent.EventType.PRODUCT_APPROVED,
            product_id=id,
            payload={
                'idempotency_key': idempotency_key,
                'product_id': str(id),
                'moderated_at': decided_at.isoformat(),
                'moderator': moderator,
                'result': 'MODERATED',
                'status': 'MODERATED',
                'blocking_reason': None,
                'field_reports': [],
            },
        )

        return Response({'product_id': id, 'status': 'MODERATED'})


@extend_schema_view(
    post=extend_schema(
        operation_id='moderation_decline_product',
        request=DeclineRequestSerializer,
        responses=OpenApiTypes.OBJECT,
    ),
)
class ProductDeclineView(APIView):
    serializer_class = DeclineRequestSerializer

    @transaction.atomic
    def post(self, request, id):
        auth_context, error = _authorize_moderator(request)
        if error:
            return error

        moderator = auth_context.actor

        serializer = DeclineRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return _error('Invalid decline payload', 'BAD_REQUEST', status.HTTP_400_BAD_REQUEST)

        reason = BlockingReason.objects.filter(code=serializer.validated_data['reason_code'], is_active=True).first()
        if not reason:
            return _error('Blocking reason does not exist', 'REASON_NOT_FOUND', status.HTTP_400_BAD_REQUEST)

        open_cards = _open_cards_queryset(id)
        if not open_cards.exists():
            return _error('Product is not found in moderation queue', 'NOT_FOUND', status.HTTP_404_NOT_FOUND)

        decline_comment = serializer.validated_data.get('comment', '')
        decline_fields = serializer.validated_data.get('fields', [])
        decided_at = datetime.now(timezone.utc)
        open_cards.update(
            queue_status=ModerationCard.QueueStatus.DECLINED,
            decline_reason=reason,
            decline_comment=decline_comment,
            decline_fields=decline_fields,
            decided_by=moderator,
            decided_at=decided_at,
            updated_at=decided_at,
        )

        blocking_reason = {
            'code': reason.code,
            'title': reason.title,
            'comment': decline_comment,
        }
        field_reports = _build_field_reports(decline_fields, decline_comment or reason.title)
        idempotency_key = str(uuid4())
        ModerationEvent.objects.create(
            event_type=ModerationEvent.EventType.PRODUCT_DECLINED,
            product_id=id,
            payload={
                'idempotency_key': idempotency_key,
                'product_id': str(id),
                'moderated_at': decided_at.isoformat(),
                'moderator': moderator,
                'result': 'BLOCKED',
                'status': 'BLOCKED',
                'blocking_reason': blocking_reason,
                'field_reports': field_reports,
                'reason': {
                    'code': reason.code,
                    'title': reason.title,
                    'comment': decline_comment,
                    'fields': decline_fields,
                },
            },
        )

        return Response(
            {
                'product_id': id,
                'status': 'BLOCKED',
                'reason': {
                    'code': reason.code,
                    'title': reason.title,
                    'comment': decline_comment,
                    'fields': decline_fields,
                },
            }
        )


@extend_schema_view(
    post=extend_schema(operation_id='moderation_receive_product_event', request=OpenApiTypes.OBJECT, responses=OpenApiTypes.OBJECT),
)
class ProductEventsView(APIView):
    def post(self, request):
        return apply_product_event(request)


@extend_schema_view(
    get=extend_schema(operation_id='moderation_list_blocking_reasons', responses=BlockingReasonSerializer(many=True)),
)
class BlockingReasonsView(APIView):
    serializer_class = BlockingReasonSerializer

    def get(self, request):
        auth_context, error = _authorize_moderator(request)
        if error:
            return error

        reasons = BlockingReason.objects.filter(is_active=True).order_by('title')
        return Response(BlockingReasonSerializer(reasons, many=True).data)
