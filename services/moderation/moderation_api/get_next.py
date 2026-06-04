from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import ModerationCard

PRIORITY_QUEUES = (1, 2, 3, 4)


def _in_review_timeout_cutoff():
    minutes = int(getattr(settings, "MODERATION_IN_REVIEW_TIMEOUT_MINUTES", 30))
    return timezone.now() - timedelta(minutes=minutes)


def release_stale_in_review_cards():
    cutoff = _in_review_timeout_cutoff()
    now = timezone.now()
    return ModerationCard.objects.filter(
        queue_status=ModerationCard.QueueStatus.IN_REVIEW,
        review_started_at__lt=cutoff,
    ).update(
        queue_status=ModerationCard.QueueStatus.PENDING,
        assigned_to=None,
        review_started_at=None,
        updated_at=now,
    )


def _moderator_has_active_review(moderator):
    cutoff = _in_review_timeout_cutoff()
    return ModerationCard.objects.filter(
        queue_status=ModerationCard.QueueStatus.IN_REVIEW,
        assigned_to=moderator,
        review_started_at__gte=cutoff,
    ).exists()


def _pick_pending_card(queue_id=None):
    base = ModerationCard.objects.select_for_update(skip_locked=True).filter(
        queue_status=ModerationCard.QueueStatus.PENDING,
    )
    queues = (queue_id,) if queue_id in PRIORITY_QUEUES else PRIORITY_QUEUES
    for priority in queues:
        card = base.filter(priority_queue=priority).order_by("created_at", "id").first()
        if card:
            return card
    return None


@transaction.atomic
def acquire_next_card(moderator, queue_id=None):
    """
    Returns (card, error_code).
    error_code: None | 'MODERATOR_ALREADY_HAS_CARD' | 'QUEUE_EMPTY'
    """
    if queue_id is not None:
        try:
            queue_id = int(queue_id)
        except (TypeError, ValueError):
            queue_id = None
        if queue_id not in PRIORITY_QUEUES:
            queue_id = None

    release_stale_in_review_cards()

    if _moderator_has_active_review(moderator):
        return None, "MODERATOR_ALREADY_HAS_CARD"

    card = _pick_pending_card(queue_id)
    if not card:
        return None, "QUEUE_EMPTY"

    now = timezone.now()
    card.queue_status = ModerationCard.QueueStatus.IN_REVIEW
    card.assigned_to = moderator
    card.review_started_at = now
    card.save(update_fields=["queue_status", "assigned_to", "review_started_at", "updated_at"])
    return card, None
