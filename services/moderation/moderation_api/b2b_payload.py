from datetime import datetime, timezone
from uuid import NAMESPACE_URL, uuid5


def _occurred_at(decided_at=None):
    moment = decided_at or datetime.now(timezone.utc)
    return moment.isoformat()


def _idempotency_key(card, action):
    return str(uuid5(NAMESPACE_URL, f'moderation:{action}:{card.id}'))


def build_approve_payload(card, decided_at=None):
    idempotency_key = _idempotency_key(card, 'approve')
    return {
        'idempotency_key': idempotency_key,
        'product_id': str(card.product_id),
        'event_type': 'MODERATED',
        'occurred_at': _occurred_at(decided_at),
        'hard_block': False,
        'blocking_reason_id': None,
        'field_reports': [],
    }, idempotency_key


def build_block_payload(card, reason, field_reports, *, hard_block, decided_at=None):
    action = 'hard-block' if hard_block else 'soft-block'
    idempotency_key = _idempotency_key(card, action)
    return {
        'idempotency_key': idempotency_key,
        'product_id': str(card.product_id),
        'event_type': 'BLOCKED',
        'occurred_at': _occurred_at(decided_at),
        'hard_block': hard_block,
        'blocking_reason_id': str(reason.reason_uuid),
        'field_reports': field_reports,
    }, idempotency_key
