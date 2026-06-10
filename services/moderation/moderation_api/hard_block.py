from datetime import datetime, timezone

from django.db import transaction

from .approve import _get_in_review_card
from .b2b_delivery import deliver_moderation_decision
from .b2b_payload import build_block_payload
from .field_reports import normalize_field_reports
from .models import ModerationCard, ModerationEvent
from .ticket_response import build_ticket_response


class HardBlockError(Exception):
    def __init__(self, code, message, http_status):
        self.code = code
        self.message = message
        self.http_status = http_status
        super().__init__(message)


def _resolve_hard_reason(reason):
    if not reason.hard_only:
        raise HardBlockError(
            'SOFT_REASON_REQUIRES_SOFT_BLOCK',
            'This blocking reason requires soft decline without hard_block',
            400,
        )
    return reason


def _validate_hard_block(card, moderator):
    if not card:
        raise HardBlockError('NOT_FOUND', 'Ticket is not in moderation review', 404)

    if card.assigned_to != moderator:
        raise HardBlockError(
            'HARD_BLOCK_NOT_ASSIGNED',
            'Card is assigned to another moderator',
            403,
        )


@transaction.atomic
def hard_block_ticket(ticket_id, moderator, reason, comment='', field_reports=None):
    from .terminal import assert_product_not_hard_blocked

    card = ModerationCard.objects.filter(id=ticket_id).first()
    if not card:
        raise HardBlockError('NOT_FOUND', 'Ticket is not in moderation review', 404)

    assert_product_not_hard_blocked(card.product_id, HardBlockError)

    reason = _resolve_hard_reason(reason)
    normalized_reports = normalize_field_reports(field_reports, error_cls=HardBlockError)

    card = _get_in_review_card(ticket_id)
    _validate_hard_block(card, moderator)

    decided_at = datetime.now(timezone.utc)
    b2b_payload, idempotency_key = build_block_payload(
        card,
        reason,
        normalized_reports,
        hard_block=True,
        decided_at=decided_at,
    )

    existing_event = ModerationEvent.objects.filter(
        product_id=card.product_id,
        event_type=ModerationEvent.EventType.PRODUCT_DECLINED,
        payload__idempotency_key=idempotency_key,
        published=True,
    ).first()
    if existing_event:
        card.refresh_from_db()
        return build_ticket_response(card)

    deliver_moderation_decision(b2b_payload, error_cls=HardBlockError)

    snapshot = dict(card.snapshot_after or {})
    snapshot['status'] = 'HARD_BLOCKED'
    snapshot['id'] = str(card.product_id)

    card.queue_status = ModerationCard.QueueStatus.HARD_BLOCKED
    card.snapshot_after = snapshot
    card.decline_reason = reason
    card.decline_comment = comment
    card.decline_fields = normalized_reports
    card.decided_by = moderator
    card.decided_at = decided_at
    card.assigned_to = None
    card.review_started_at = None
    card.save(
        update_fields=[
            'queue_status',
            'snapshot_after',
            'decline_reason',
            'decline_comment',
            'decline_fields',
            'decided_by',
            'decided_at',
            'assigned_to',
            'review_started_at',
            'updated_at',
        ]
    )

    ModerationEvent.objects.create(
        event_type=ModerationEvent.EventType.PRODUCT_DECLINED,
        product_id=card.product_id,
        published=True,
        payload={
            **b2b_payload,
            'moderated_at': decided_at.isoformat(),
            'moderator': moderator,
            'result': 'HARD_BLOCKED',
            'card_id': str(card.id),
        },
    )

    return build_ticket_response(card)
