import json
from uuid import UUID

import requests
from django.conf import settings
from django.utils import timezone

from .models import ModerationCard


def _parse_event_payload(data: dict):
    raw_payload = data.get("payload")
    if raw_payload:
        try:
            return json.loads(raw_payload)
        except (TypeError, ValueError):
            return {}
    return {}


def parse_event(message_fields: dict) -> dict:
    fields = {k.decode("utf-8") if isinstance(k, bytes) else k: v for k, v in message_fields.items()}
    normalized = {
        (k.decode("utf-8") if isinstance(k, bytes) else k): (v.decode("utf-8") if isinstance(v, bytes) else v)
        for k, v in fields.items()
    }

    payload = _parse_event_payload(normalized)
    product_id = normalized.get("product_id") or payload.get("product_id") or normalized.get("aggregate_id")
    raw_event_type = normalized.get("event_type")
    payload_event_type = payload.get("event_type")
    if raw_event_type in {"PRODUCT_CREATED", "PRODUCT_UPDATED", "PRODUCT_DELETED"} and payload_event_type:
        event_type = str(payload_event_type).upper()
    else:
        event_type = (raw_event_type or payload_event_type or "UPDATED").upper()

    return {
        "source": normalized.get("source"),
        "product_id": product_id,
        "event_type": event_type,
        "snapshot_before": payload.get("snapshot_before"),
        "snapshot_after": payload.get("snapshot_after"),
    }


def fetch_b2b_snapshot(product_id):
    template = settings.B2B_PRODUCT_URL_TEMPLATE
    if not template:
        return None

    url = template.format(product_id=product_id)
    try:
        response = requests.get(url, timeout=settings.B2B_REQUEST_TIMEOUT)
    except requests.RequestException:
        return None

    if not response.ok:
        return None

    try:
        return response.json()
    except ValueError:
        return None


def _open_cards_queryset(product_id):
    return ModerationCard.objects.filter(
        product_id=product_id,
        queue_status__in=[
            ModerationCard.QueueStatus.PENDING,
            ModerationCard.QueueStatus.IN_REVIEW,
        ],
    ).order_by("created_at", "id")


def _has_live_skus(snapshot):
    skus = snapshot.get("skus") or []
    return any(not bool(sku.get("deleted")) for sku in skus if isinstance(sku, dict))


def _requires_moderation(snapshot):
    return snapshot.get("status") == "ON_MODERATION" and _has_live_skus(snapshot)


def _archive_cards(product_id):
    now = timezone.now()
    ModerationCard.objects.filter(product_id=product_id).exclude(
        queue_status=ModerationCard.QueueStatus.ARCHIVED
    ).update(queue_status=ModerationCard.QueueStatus.ARCHIVED, updated_at=now)


def _delete_open_cards(product_id):
    _open_cards_queryset(product_id).delete()


def _card_event_type(event_type):
    normalized = str(event_type or ModerationCard.EventType.UPDATED).upper()
    if normalized in {
        ModerationCard.EventType.CREATED,
        ModerationCard.EventType.EDITED,
        ModerationCard.EventType.UPDATED,
    }:
        return normalized
    return ModerationCard.EventType.EDITED


def enqueue_from_event(event: dict):
    if not event.get("product_id"):
        return None

    if event.get("source") not in {None, "", "b2b"}:
        return None

    try:
        product_id = UUID(str(event["product_id"]))
    except (TypeError, ValueError):
        return None

    event_type = str(event.get("event_type", ModerationCard.EventType.EDITED)).upper()
    if event_type == "DELETED":
        _archive_cards(product_id)
        return None

    stored_event_type = _card_event_type(event_type)
    snapshot_after = event.get("snapshot_after") or fetch_b2b_snapshot(product_id) or {"id": str(product_id)}
    if not _requires_moderation(snapshot_after):
        _delete_open_cards(product_id)
        return None

    snapshot_before = event.get("snapshot_before")
    existing_cards = list(_open_cards_queryset(product_id))
    if existing_cards:
        card = existing_cards[0]
        card.event_type = stored_event_type
        card.snapshot_before = snapshot_before if snapshot_before is not None else card.snapshot_before
        card.snapshot_after = snapshot_after
        if (
            card.queue_status == ModerationCard.QueueStatus.IN_REVIEW
            and stored_event_type == ModerationCard.EventType.EDITED
        ):
            update_fields = ["event_type", "snapshot_before", "snapshot_after", "updated_at"]
        else:
            card.queue_status = ModerationCard.QueueStatus.PENDING
            card.assigned_to = None
            card.decided_by = None
            card.decided_at = None
            card.decline_reason = None
            card.decline_comment = ""
            card.decline_fields = []
            update_fields = [
                "event_type",
                "queue_status",
                "snapshot_before",
                "snapshot_after",
                "assigned_to",
                "decided_by",
                "decided_at",
                "decline_reason",
                "decline_comment",
                "decline_fields",
                "updated_at",
            ]
        card.save(update_fields=update_fields)
        for duplicate in existing_cards[1:]:
            duplicate.delete()
        return card

    return ModerationCard.objects.create(
        product_id=product_id,
        event_type=stored_event_type,
        snapshot_before=snapshot_before,
        snapshot_after=snapshot_after,
    )
