import json

from django.conf import settings
from django.core.management.base import BaseCommand
from redis import Redis

from moderation_api.models import ModerationEvent


class Command(BaseCommand):
    help = 'Publish moderation events to Redis stream'

    def handle(self, *args, **options):
        client = Redis.from_url(settings.REDIS_URL)
        events = list(ModerationEvent.objects.filter(published=False).order_by('created_at')[:200])
        if not events:
            self.stdout.write('No events to publish')
            return

        for event in events:
            client.xadd(
                settings.MODERATION_EVENTS_STREAM,
                {
                    'source': settings.EVENT_SOURCE,
                    'event_type': event.event_type,
                    'aggregate_id': str(event.product_id),
                    'payload': json.dumps(event.payload),
                },
            )
            ModerationEvent.objects.filter(id=event.id).update(published=True)

        self.stdout.write(f'Published {len(events)} moderation events')
