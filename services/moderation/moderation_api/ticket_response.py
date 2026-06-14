from .models import ModerationCard

TICKET_KIND_MAP = {
    ModerationCard.EventType.CREATED: 'CREATE',
    ModerationCard.EventType.UPDATED: 'EDIT',
    ModerationCard.EventType.EDITED: 'EDIT',
}


def ticket_status(card):
    status = card.queue_status
    if status == ModerationCard.QueueStatus.DECLINED:
        return 'BLOCKED'
    return status


def ticket_kind(card):
    return TICKET_KIND_MAP.get(card.event_type, 'EDIT')


def build_ticket_response(card):
    snapshot = card.snapshot_after or {}
    seller_id = snapshot.get('seller_id')
    return {
        'id': str(card.id),
        'product_id': str(card.product_id),
        'seller_id': str(seller_id) if seller_id else None,
        'kind': ticket_kind(card),
        'status': ticket_status(card),
        'queue_priority': card.priority_queue,
        'created_at': card.created_at.isoformat(),
    }
