from datetime import datetime, timezone

from django.db import transaction

from .approve import _get_in_review_card
from .b2b_client import post_moderation_decision
from .b2b_payload import build_block_payload
from .field_reports import normalize_field_reports
from .models import BlockingReason, ModerationCard, ModerationEvent


class HardBlockError(Exception):
    def __init__(self, code, message, http_status):
        self.code = code
        self.message = message
        self.http_status = http_status
        super().__init__(message)


def _deliver_to_b2b(payload):
    result, error_kind = post_moderation_decision(payload)
    if error_kind == 'unconfigured':
        raise HardBlockError('B2B_NOT_CONFIGURED', 'B2B moderation endpoint is not configured', 503)
    if error_kind == 'unavailable':
        raise HardBlockError('B2B_UNAVAILABLE', 'B2B service is temporarily unavailable', 503)
    http_status, _data = result
    if http_status == 404:
        raise HardBlockError('PRODUCT_NOT_FOUND', 'Product not found in B2B catalog', 404)
    if http_status not in (200, 201):
        raise HardBlockError('B2B_UNAVAILABLE', 'B2B service rejected moderation decision', 503)


def _resolve_hard_reason(blocking_reason_id):
    reason = BlockingReason.objects.filter(code=blocking_reason_id, is_active=True).first()
    if not reason:
        raise HardBlockError(
            'BLOCKING_REASON_NOT_FOUND',
            'Blocking reason does not exist',
            400,
        )
    if not reason.hard_only:
        raise HardBlockError(
            'SOFT_REASON_REQUIRES_SOFT_BLOCK',
            'This blocking reason requires soft decline without hard_block',
            400,
        )
    return reason


def _validate_hard_block(card, moderator):
    if not card:
        raise HardBlockError('NOT_FOUND', 'Product is not in moderation review', 404)

    if card.assigned_to != moderator:
        raise HardBlockError(
            'HARD_BLOCK_NOT_ASSIGNED',
            'Card is assigned to another moderator',
            403,
        )


@transaction.atomic
def hard_block_product(product_id, moderator, blocking_reason_id, comment='', field_reports=None):
    from .terminal import assert_product_not_hard_blocked

    assert_product_not_hard_blocked(product_id, HardBlockError)

    reason = _resolve_hard_reason(blocking_reason_id)
    normalized_reports = normalize_field_reports(field_reports, error_cls=HardBlockError)

    card = _get_in_review_card(product_id)
    _validate_hard_block(card, moderator)

    decided_at = datetime.now(timezone.utc)
    b2b_payload, idempotency_key = build_block_payload(
        card,
        reason,
        normalized_reports,
        hard_block=True,
        idempotency_prefix='hard-block',
        decided_at=decided_at,
    )

    existing_event = ModerationEvent.objects.filter(
        product_id=product_id,
        event_type=ModerationEvent.EventType.PRODUCT_DECLINED,
        payload__idempotency_key=idempotency_key,
        published=True,
    ).first()
    if existing_event:
        return _response_payload(product_id, reason, comment, normalized_reports)

    _deliver_to_b2b(b2b_payload)

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
        product_id=product_id,
        published=True,
        payload={
            **b2b_payload,
            'moderated_at': decided_at.isoformat(),
            'moderator': moderator,
            'result': 'HARD_BLOCKED',
            'card_id': str(card.id),
        },
    )

    return _response_payload(product_id, reason, comment, normalized_reports)


def _response_payload(product_id, reason, comment, field_reports):
    return {
        'product_id': str(product_id),
        'status': 'HARD_BLOCKED',
        'hard_block': True,
        'field_reports': field_reports,
        'reason': {
            'code': reason.code,
            'title': reason.title,
            'comment': comment,
        },
    }
