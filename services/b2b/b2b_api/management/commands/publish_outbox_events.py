import json

import redis
from django.conf import settings
from django.core.management.base import BaseCommand

from b2b_api.models import IntegrationOutbox


class Command(BaseCommand):
    help = 'Publish B2B outbox events to Redis stream'

    def handle(self, *args, **options):
        client = redis.Redis.from_url(settings.REDIS_URL)
        events = list(IntegrationOutbox.objects.filter(published=False).order_by('id')[:200])
        if not events:
            self.stdout.write('No events to publish')
            return

        for event in events:
            client.xadd(
                settings.EVENT_STREAM,
                {
                    'source': settings.EVENT_SOURCE,
                    'event_type': event.event_type,
                    'aggregate_id': str(event.aggregate_id),
                    'payload': json.dumps(event.payload),
                },
            )
            IntegrationOutbox.objects.filter(id=event.id).update(published=True)

        self.stdout.write(f'Published {len(events)} events')
