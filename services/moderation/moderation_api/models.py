import uuid

from django.db import models


class BlockingReason(models.Model):
    reason_uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True, db_index=True)
    code = models.SlugField(primary_key=True, max_length=64)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    hard_only = models.BooleanField(
        default=False,
        help_text='Причина только для жёсткой блокировки (US-MOD-05), не для soft decline.',
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['title']

    def __str__(self):
        return self.title

    def delete(self, *args, **kwargs):
        from django.db.models.deletion import ProtectedError

        if ModerationCard.objects.filter(decline_reason=self).exists():
            raise ProtectedError(
                'Blocking reason is referenced by moderation cards; deactivate instead of deleting.',
                [self],
            )
        return super().delete(*args, **kwargs)


class ModerationCard(models.Model):
    class EventType(models.TextChoices):
        CREATED = 'CREATED', 'CREATED'
        UPDATED = 'UPDATED', 'UPDATED'
        EDITED = 'EDITED', 'EDITED'

    class QueueStatus(models.TextChoices):
        PENDING = 'PENDING', 'PENDING'
        IN_REVIEW = 'IN_REVIEW', 'IN_REVIEW'
        APPROVED = 'APPROVED', 'APPROVED'
        BLOCKED = 'BLOCKED', 'BLOCKED'
        DECLINED = 'DECLINED', 'DECLINED'
        HARD_BLOCKED = 'HARD_BLOCKED', 'HARD_BLOCKED'
        ARCHIVED = 'ARCHIVED', 'ARCHIVED'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    product_id = models.UUIDField(db_index=True)
    event_type = models.CharField(max_length=16, choices=EventType.choices)
    queue_status = models.CharField(max_length=16, choices=QueueStatus.choices, default=QueueStatus.PENDING, db_index=True)

    snapshot_before = models.JSONField(null=True, blank=True)
    snapshot_after = models.JSONField(default=dict)

    priority_queue = models.PositiveSmallIntegerField(default=1, db_index=True)
    assigned_to = models.CharField(max_length=255, null=True, blank=True)
    review_started_at = models.DateTimeField(null=True, blank=True, db_index=True)
    decided_by = models.CharField(max_length=255, null=True, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)

    decline_reason = models.ForeignKey(
        BlockingReason,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='cards',
    )
    decline_comment = models.CharField(max_length=500, blank=True)
    decline_fields = models.JSONField(default=list, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['created_at']
        indexes = [
            models.Index(fields=['queue_status', 'created_at']),
            models.Index(fields=['product_id', 'queue_status']),
        ]


TERMINAL_QUEUE_STATUSES = frozenset({ModerationCard.QueueStatus.HARD_BLOCKED})


class ModerationEvent(models.Model):
    class EventType(models.TextChoices):
        PRODUCT_APPROVED = 'PRODUCT_APPROVED', 'PRODUCT_APPROVED'
        PRODUCT_DECLINED = 'PRODUCT_DECLINED', 'PRODUCT_DECLINED'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event_type = models.CharField(max_length=32, choices=EventType.choices)
    product_id = models.UUIDField(db_index=True)
    payload = models.JSONField(default=dict)
    published = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


class IntegrationInbox(models.Model):
    message_id = models.CharField(max_length=128, primary_key=True)
    source = models.CharField(max_length=64)
    event_type = models.CharField(max_length=64)
    payload = models.JSONField(default=dict)
    received_at = models.DateTimeField(auto_now_add=True)
