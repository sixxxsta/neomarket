import os
import sys

from django.conf import settings
from django.core.management.base import BaseCommand

from b2b_api.models import Product
from b2b_api.views import apply_moderation_decision


def _build_field_reports(payload):
    reports = payload.get('field_reports')
    if reports:
        return reports

    reason = payload.get('reason') or {}
    message = reason.get('comment') or reason.get('title') or 'Требуется исправление после модерации'
    return [
        {
            'field': str(field or '').strip(),
            'message': message,
        }
        for field in reason.get('fields', [])
        if str(field or '').strip()
    ]


class Command(BaseCommand):
    help = 'Consume moderation approval/decline events and apply them to B2B products'

    def handle(self, *args, **options):
        # Add parent directory to path for infra module imports
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../../../../'))
        from infra.event_consumer_retry import EventConsumerWithRetry, RetryPolicy
        from redis import Redis

        redis_client = Redis.from_url(settings.REDIS_URL)
        retry_policy = RetryPolicy(
            max_retries=5,
            initial_delay_ms=200,
            max_delay_ms=30000,
            backoff_multiplier=2.0,
            jitter=True,
        )

        consumer = EventConsumerWithRetry(
            redis_client=redis_client,
            service_name='b2b',
            source='b2b-moderation',
            retry_policy=retry_policy,
        )

        self.stdout.write('Starting B2B moderation consumer with retry/DLQ support')
        consumer.consume_with_retry(
            handler=self._handle_event,
            batch_size=20,
            block_ms=5000,
        )

    def _handle_event(self, source: str, event_type: str, payload: dict):
        if source != 'moderation' or event_type not in {'PRODUCT_APPROVED', 'PRODUCT_DECLINED'}:
            return

        product_id = payload.get('product_id')
        if not product_id:
            return

        apply_moderation_decision(
            {
                'idempotency_key': payload.get('idempotency_key') or f'{event_type}:{product_id}:{payload.get("moderated_at", "")}',
                'product_id': product_id,
                'status': Product.Status.MODERATED if event_type == 'PRODUCT_APPROVED' else Product.Status.BLOCKED,
                'hard_block': bool(payload.get('hard_block')),
                'blocking_reason': payload.get('blocking_reason') or payload.get('reason'),
                'field_reports': _build_field_reports(payload),
            }
        )
