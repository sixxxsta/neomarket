import logging

from django.conf import settings
from django.utils import timezone

from .models import IntegrationOutbox, Order

logger = logging.getLogger(__name__)


def build_fulfill_payload(order):
    items = [
        {"sku_id": str(item.sku_id), "quantity": item.quantity}
        for item in order.items.all()
    ]
    return {"order_id": str(order.id), "items": items}


def attempt_fulfill(order):
    """
    Synchronously call B2B fulfill for a DELIVERED order.
    Returns (success, http_status_or_none, error_kind_or_none).
    """
    if order.fulfilled_at:
        return True, 200, None

    from .inventory_client import inventory_call

    payload = build_fulfill_payload(order)
    result, error_kind = inventory_call(settings.B2B_FULFILL_URL, payload)
    if error_kind == "unavailable":
        logger.warning("B2B fulfill unavailable for order %s; will retry asynchronously", order.id)
        return False, None, error_kind

    http_status, _data = result
    if http_status != 200:
        logger.warning(
            "B2B fulfill returned %s for order %s; will retry asynchronously",
            http_status,
            order.id,
        )
        return False, http_status, None

    order.fulfilled_at = timezone.now()
    order.save(update_fields=["fulfilled_at", "updated_at"])
    return True, http_status, None


def enqueue_fulfill_retry(order_id):
    exists = IntegrationOutbox.objects.filter(
        aggregate_id=order_id,
        event_type="ORDER_FULFILL_PENDING",
        published=False,
    ).exists()
    if exists:
        return
    IntegrationOutbox.objects.create(
        aggregate_id=order_id,
        event_type="ORDER_FULFILL_PENDING",
        payload={"order_id": str(order_id)},
    )


def trigger_fulfill_on_delivered(order):
    success, _, _ = attempt_fulfill(order)
    if not success:
        enqueue_fulfill_retry(order.id)
