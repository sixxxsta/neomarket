from datetime import datetime, timezone

from django.db import transaction

from .b2b_delivery import deliver_moderation_decision
from .b2b_payload import build_approve_payload
from .models import ModerationCard, ModerationEvent
from .queue import _has_live_skus
from .ticket_response import build_ticket_response


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


def _get_in_review_card(ticket_id):
    return (
        ModerationCard.objects.select_for_update()
        .filter(
            id=ticket_id,
            queue_status=ModerationCard.QueueStatus.IN_REVIEW,
        )
        .first()
    )


def _validate_approve(card, moderator):
    if not card:
        raise ApproveError('NOT_FOUND', 'Ticket is not in moderation review', 404)

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
def approve_ticket(ticket_id, moderator):
    from .terminal import assert_product_not_hard_blocked

    card = ModerationCard.objects.filter(id=ticket_id).first()
    if not card:
        raise ApproveError('NOT_FOUND', 'Ticket is not in moderation review', 404)

    assert_product_not_hard_blocked(card.product_id, ApproveError)

    card = _get_in_review_card(ticket_id)
    _validate_approve(card, moderator)

    decided_at = datetime.now(timezone.utc)
    b2b_payload, idempotency_key = build_approve_payload(card, decided_at)

    existing_event = ModerationEvent.objects.filter(
        product_id=card.product_id,
        event_type=ModerationEvent.EventType.PRODUCT_APPROVED,
        payload__idempotency_key=idempotency_key,
        published=True,
    ).first()
    if existing_event:
        card.refresh_from_db()
        return build_ticket_response(card)

    deliver_moderation_decision(b2b_payload, error_cls=ApproveError)

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
        product_id=card.product_id,
        published=True,
        payload={
            **b2b_payload,
            'moderated_at': decided_at.isoformat(),
            'moderator': moderator,
            'result': 'MODERATED',
            'card_id': str(card.id),
        },
    )

    return build_ticket_response(card)
