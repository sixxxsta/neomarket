from datetime import datetime, timezone


def _occurred_at(decided_at=None):
    moment = decided_at or datetime.now(timezone.utc)
    return moment.isoformat()


def build_approve_payload(card, decided_at=None):
    idempotency_key = f'approve:{card.id}'
    return {
        'idempotency_key': idempotency_key,
        'product_id': str(card.product_id),
        'event_type': 'MODERATED',
        'occurred_at': _occurred_at(decided_at),
        'hard_block': False,
        'blocking_reason_id': None,
        'field_reports': [],
    }, idempotency_key


def build_block_payload(card, reason, field_reports, *, hard_block, idempotency_prefix, decided_at=None):
    idempotency_key = f'{idempotency_prefix}:{card.id}'
    return {
        'idempotency_key': idempotency_key,
        'product_id': str(card.product_id),
        'event_type': 'BLOCKED',
        'occurred_at': _occurred_at(decided_at),
        'hard_block': hard_block,
        'blocking_reason_id': reason.code,
        'field_reports': field_reports,
    }, idempotency_key
