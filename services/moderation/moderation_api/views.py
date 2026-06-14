from datetime import datetime, timezone
from uuid import uuid4

from django.db import transaction
from .approve import ApproveError, approve_ticket
from .decline import BlockError, block_ticket
from .hard_block import HardBlockError
from .soft_block import SoftBlockError
from .terminal import assert_product_not_hard_blocked
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, extend_schema_view
from jwt import InvalidTokenError
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .auth import authenticate_request, has_any_role
from .models import BlockingReason, ModerationCard, ModerationEvent
from .get_next import acquire_next_card
from .product_events import apply_product_event
from .serializers import (
    BlockDecisionRequestSerializer,
    BlockingReasonSerializer,
    EnqueueRequestSerializer,
    GetNextRequestSerializer,
    ModerationCardSerializer,
    TicketResponseSerializer,
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
    post=extend_schema(
        operation_id='moderation_get_next_card',
        request=GetNextRequestSerializer,
        responses=ModerationCardSerializer,
    ),
)
class ModerationNextCardView(APIView):
    serializer_class = ModerationCardSerializer

    def post(self, request):
        auth_context, error = _authorize_moderator(request)
        if error:
            return error

        serializer = GetNextRequestSerializer(data=request.data or {})
        if not serializer.is_valid():
            return _error('Invalid get-next payload', 'BAD_REQUEST', status.HTTP_400_BAD_REQUEST)

        moderator = auth_context.actor
        card, error_code = acquire_next_card(moderator, serializer.validated_data.get('queue_id'))
        if error_code == 'MODERATOR_ALREADY_HAS_CARD':
            return _error(
                'Moderator already has a card in review',
                'MODERATOR_ALREADY_HAS_CARD',
                status.HTTP_409_CONFLICT,
            )
        if error_code == 'QUEUE_EMPTY':
            return Response(status=status.HTTP_204_NO_CONTENT)
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

        from .queue import compute_priority_queue

        product_id = serializer.validated_data['product_id']
        try:
            assert_product_not_hard_blocked(product_id, BlockError)
        except BlockError as exc:
            return _error(exc.message, exc.code, exc.http_status)
        snapshot_after = serializer.validated_data.get('snapshot_after') or {'id': str(product_id)}
        card = ModerationCard.objects.create(
            product_id=product_id,
            event_type=serializer.validated_data['event_type'],
            priority_queue=compute_priority_queue(
                product_id,
                serializer.validated_data['event_type'],
                snapshot_after,
            ),
            snapshot_before=serializer.validated_data.get('snapshot_before'),
            snapshot_after=snapshot_after,
        )
        return Response(ModerationCardSerializer(card).data, status=status.HTTP_201_CREATED)


@extend_schema_view(
    post=extend_schema(operation_id='moderation_approve_ticket', responses=TicketResponseSerializer),
)
class TicketApproveView(APIView):
    serializer_class = TicketResponseSerializer

    def post(self, request, ticket_id):
        auth_context, error = _authorize_moderator(request)
        if error:
            return error

        try:
            result = approve_ticket(ticket_id, auth_context.actor)
        except ApproveError as exc:
            return _error(exc.message, exc.code, exc.http_status)

        return Response(result)


@extend_schema_view(
    post=extend_schema(
        operation_id='moderation_block_ticket',
        request=BlockDecisionRequestSerializer,
        responses=TicketResponseSerializer,
    ),
)
class TicketBlockView(APIView):
    serializer_class = BlockDecisionRequestSerializer

    def post(self, request, ticket_id):
        auth_context, error = _authorize_moderator(request)
        if error:
            return error

        serializer = BlockDecisionRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return _error('Invalid block payload', 'BAD_REQUEST', status.HTTP_400_BAD_REQUEST)

        legacy_fields = serializer.validated_data.get('fields') or []
        field_reports = serializer.validated_data.get('field_reports') or []
        if not field_reports and legacy_fields:
            field_reports = [
                {'field_path': field_name, 'message': serializer.validated_data.get('comment', '')}
                for field_name in legacy_fields
            ]

        try:
            result = block_ticket(
                ticket_id,
                auth_context.actor,
                serializer.validated_data['blocking_reason_ids'],
                comment=serializer.validated_data.get('comment', ''),
                field_reports=field_reports,
            )
        except (BlockError, SoftBlockError, HardBlockError) as exc:
            return _error(exc.message, exc.code, exc.http_status)

        return Response(result)


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

        from .blocking_reasons import list_active_blocking_reasons

        hard_block = request.query_params.get('hard_block')
        if hard_block is not None:
            normalized = str(hard_block).strip().lower()
            if normalized not in {'true', 'false', '1', '0'}:
                return _error('hard_block must be true or false', 'INVALID_PARAMETER', status.HTTP_400_BAD_REQUEST)
            hard_block = normalized in {'true', '1'}
        else:
            hard_block = None

        reasons = list_active_blocking_reasons(hard_block=hard_block)
        return Response(BlockingReasonSerializer(reasons, many=True).data)
