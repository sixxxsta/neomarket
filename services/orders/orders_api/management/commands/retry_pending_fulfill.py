from django.core.management.base import BaseCommand

from orders_api.fulfill import attempt_fulfill
from orders_api.models import IntegrationOutbox, Order


class Command(BaseCommand):
    help = "Retry B2B fulfill for orders with ORDER_FULFILL_PENDING outbox entries"

    def handle(self, *args, **options):
        pending = IntegrationOutbox.objects.filter(
            event_type="ORDER_FULFILL_PENDING",
            published=False,
        ).order_by("created_at")

        processed = 0
        for entry in pending:
            order = (
                Order.objects.filter(id=entry.aggregate_id, status=Order.Status.DELIVERED)
                .prefetch_related("items")
                .first()
            )
            if not order:
                entry.published = True
                entry.save(update_fields=["published"])
                self.stdout.write(f"Skipped stale fulfill retry for order {entry.aggregate_id}")
                continue

            success, _, _ = attempt_fulfill(order)
            if success:
                entry.published = True
                entry.save(update_fields=["published"])
                processed += 1
                self.stdout.write(f"Fulfill completed for order {order.id}")
            else:
                self.stdout.write(f"Fulfill still pending for order {order.id}")

        self.stdout.write(f"Processed {processed} fulfill retries")
