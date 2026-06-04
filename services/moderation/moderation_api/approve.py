from datetime import datetime, timezone

from django.db import transaction
from .b2b_client import post_moderation_decision
from .models import ModerationCard, ModerationEvent
from .queue import _has_live_skus


class ApproveError(Exception):
    def __init__(self, code, message, http_status):
        self.code = code
        self.message = message
        self.http_status = http_status
        super().__init__(message)


def _edited_during_review(card):
    if card.queue_status != ModerationCard.QueueStatus.IN_REVIEW:
        return False
    if card.event_type != ModerationCard.EventType.EDITED:
        return False
    if not card.review_started_at:
        return False
    return card.updated_at > card.review_started_at


def _build_b2b_payload(card, moderator, decided_at):
    idempotency_key = f'approve:{card.id}'
    return {
        'idempotency_key': idempotency_key,
        'product_id': str(card.product_id),
        'status': 'MODERATED',
        'hard_block': False,
        'blocking_reason': None,
        'field_reports': [],
    }, idempotency_key


def _deliver_to_b2b(payload):
    result, error_kind = post_moderation_decision(payload)
    if error_kind == 'unconfigured':
        raise ApproveError('B2B_NOT_CONFIGURED', 'B2B moderation endpoint is not configured', 503)
    if error_kind == 'unavailable':
        raise ApproveError('B2B_UNAVAILABLE', 'B2B service is temporarily unavailable', 503)
    http_status, _data = result
    if http_status == 404:
        raise ApproveError('PRODUCT_NOT_FOUND', 'Product not found in B2B catalog', 404)
    if http_status not in (200, 201):
        raise ApproveError('B2B_UNAVAILABLE', 'B2B service rejected moderation decision', 503)
    return True


def _get_in_review_card(product_id):
    return (
        ModerationCard.objects.select_for_update()
        .filter(
            product_id=product_id,
            queue_status=ModerationCard.QueueStatus.IN_REVIEW,
        )
        .order_by('-review_started_at', '-created_at')
        .first()
    )


def _validate_approve(card, moderator):
    if not card:
        raise ApproveError('NOT_FOUND', 'Product is not in moderation review', 404)

    if card.assigned_to != moderator:
        raise ApproveError(
            'APPROVE_NOT_ASSIGNED',
            'Card is assigned to another moderator',
            403,
        )

    snapshot = card.snapshot_after or {}
    if snapshot.get('status') == 'HARD_BLOCKED':
        raise ApproveError(
            'CANNOT_APPROVE_HARD_BLOCKED',
            'Hard-blocked products cannot be approved',
            409,
        )

    if not _has_live_skus(snapshot):
        raise ApproveError(
            'APPROVE_WITHOUT_SKU',
            'Product must have at least one active SKU',
            409,
        )

    if _edited_during_review(card):
        raise ApproveError(
            'APPROVE_AFTER_EDITED',
            'Seller edited the product during review; take the card again',
            409,
        )


@transaction.atomic
def approve_product(product_id, moderator):
    card = _get_in_review_card(product_id)
    _validate_approve(card, moderator)

    decided_at = datetime.now(timezone.utc)
    b2b_payload, idempotency_key = _build_b2b_payload(card, moderator, decided_at)

    existing_event = ModerationEvent.objects.filter(
        product_id=product_id,
        event_type=ModerationEvent.EventType.PRODUCT_APPROVED,
        payload__idempotency_key=idempotency_key,
        published=True,
    ).first()
    if existing_event:
        return {'product_id': str(product_id), 'status': 'MODERATED'}

    _deliver_to_b2b(b2b_payload)

    card.queue_status = ModerationCard.QueueStatus.APPROVED
    card.decided_by = moderator
    card.decided_at = decided_at
    card.assigned_to = None
    card.review_started_at = None
    card.save(
        update_fields=[
            'queue_status',
            'decided_by',
            'decided_at',
            'assigned_to',
            'review_started_at',
            'updated_at',
        ]
    )

    ModerationEvent.objects.create(
        event_type=ModerationEvent.EventType.PRODUCT_APPROVED,
        product_id=product_id,
        published=True,
        payload={
            **b2b_payload,
            'moderated_at': decided_at.isoformat(),
            'moderator': moderator,
            'result': 'MODERATED',
            'card_id': str(card.id),
        },
    )

    return {'product_id': str(product_id), 'status': 'MODERATED'}
