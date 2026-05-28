import json
import sys
import os

from django.conf import settings
from django.core.management.base import BaseCommand
from redis import Redis

# Add parent directory to path for infra module imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../../../../'))
from infra.event_consumer_retry import EventConsumerWithRetry, RetryPolicy

from catalog_api.b2b_projection import sync_product_snapshot
from catalog_api.models import Category, IntegrationInbox, Product


class Command(BaseCommand):
    help = 'Consume domain events with retry logic and DLQ support, projecting state into catalog read model'

    def handle(self, *args, **options):
        redis_client = Redis.from_url(settings.REDIS_URL)
        
        # Initialize consumer with retry policy
        retry_policy = RetryPolicy(
            max_retries=5,
            initial_delay_ms=200,
            max_delay_ms=30000,
            backoff_multiplier=2.0,
            jitter=True,
        )
        
        consumer = EventConsumerWithRetry(
            redis_client=redis_client,
            service_name='catalog',
            source='catalog-consumer',
            retry_policy=retry_policy,
        )
        
        self.stdout.write("Starting catalog event consumer with retry/DLQ support")
        self.stdout.write(f"Retry policy: max_retries={retry_policy.max_retries}, initial_delay={retry_policy.initial_delay_ms}ms")
        
        # Start consuming
        consumer.consume_with_retry(
            handler=self._handle_event,
            batch_size=20,
            block_ms=5000,
        )

    def _handle_event(self, source: str, event_type: str, payload: dict):
        """Apply domain event to catalog read model."""
        
        if source == 'moderation' and event_type in {'PRODUCT_APPROVED', 'PRODUCT_DECLINED'}:
            self.stdout.write(
                f"Ignored direct moderation event {event_type} for product {payload.get('product_id')} "
                "because catalog now waits for the authoritative B2B snapshot"
            )
            return

        if source == 'b2b' and event_type in {'PRODUCT_CREATED', 'PRODUCT_UPDATED'}:
            snapshot = payload.get('snapshot_after') or {}
            product = sync_product_snapshot(snapshot)
            if product is None and snapshot.get('deleted'):
                return
            if product is not None:
                self.stdout.write(f"Upserted product {product.id} in catalog")
        elif source == 'b2b' and event_type == 'PRODUCT_DELETED':
            product_id = payload.get('product_id')
            if not product_id:
                return
            Product.objects.filter(id=product_id).delete()
            self.stdout.write(f"Deleted product {product_id} from catalog")
        elif source == 'b2b' and event_type == 'PRODUCT_BLOCKED':
            product_id = payload.get('product_id')
            if not product_id:
                return
            updated = Product.objects.filter(id=product_id).update(status=Product.Status.BLOCKED)
            if updated:
                self.stdout.write(f"Updated product {product_id} status → {Product.Status.BLOCKED}")
