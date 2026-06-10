from .models import ModerationCard


def ticket_status(card):
    status = card.queue_status
    if status == ModerationCard.QueueStatus.DECLINED:
        return 'BLOCKED'
    return status


def build_ticket_response(card):
    snapshot = card.snapshot_after or {}
    seller_id = snapshot.get('seller_id')
    return {
        'id': str(card.id),
        'product_id': str(card.product_id),
        'seller_id': str(seller_id) if seller_id else None,
        'kind': card.event_type,
        'status': ticket_status(card),
        'queue_priority': card.priority_queue,
        'created_at': card.created_at.isoformat(),
    }
