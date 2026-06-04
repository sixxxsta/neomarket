from uuid import UUID

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response

from .models import IntegrationInbox, ModerationCard
from .queue import enqueue_from_event


def _error(message, code, http_status):
    return Response({"code": code, "message": message}, status=http_status)


def _parse_uuid(value):
    if not value:
        return None
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


def _normalize_incoming_event(data):
    event_type = str(data.get("event_type") or "").strip().upper()
    return {
        "source": "b2b",
        "product_id": data.get("product_id"),
        "event_type": event_type,
        "snapshot_before": data.get("snapshot_before"),
        "snapshot_after": data.get("snapshot_after"),
    }


@transaction.atomic
def apply_product_event(request):
    from django.conf import settings

    expected = settings.INTERNAL_SERVICE_KEY
    if expected and request.headers.get("X-Service-Key") != expected:
        return _error("Неверный X-Service-Key", "UNAUTHORIZED", status.HTTP_401_UNAUTHORIZED)

    data = request.data or {}
    idempotency_key = str(data.get("idempotency_key") or "").strip()
    product_id = _parse_uuid(data.get("product_id"))
    event_type = str(data.get("event_type") or "").strip().upper()

    if not idempotency_key or not product_id or not event_type:
        return _error("Невалидный payload события", "INVALID_REQUEST", status.HTTP_400_BAD_REQUEST)

    if IntegrationInbox.objects.filter(message_id=idempotency_key).exists():
        return Response({"accepted": True})

    event = _normalize_incoming_event(data)
    card = enqueue_from_event(event)

    try:
        IntegrationInbox.objects.create(
            message_id=idempotency_key,
            source="b2b",
            event_type=event_type,
            payload=data,
        )
    except IntegrityError:
        return Response({"accepted": True})

    payload = {"accepted": True}
    if card:
        payload["card_id"] = str(card.id)
    return Response(payload)
