import time
import urllib.error
import urllib.parse
import urllib.request
import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from catalog_api.b2b_projection import sync_product_snapshot
from catalog_api.models import Product


class Command(BaseCommand):
    help = "Synchronize catalog read model from current B2B public feed"

    def add_arguments(self, parser):
        parser.add_argument("--retries", type=int, default=10)
        parser.add_argument("--sleep", type=float, default=2.0)
        parser.add_argument("--limit", type=int, default=100)

    def handle(self, *args, **options):
        retries = max(1, int(options["retries"]))
        sleep_s = max(0.0, float(options["sleep"]))
        limit = max(1, min(int(options["limit"]), 500))

        payload = self._fetch_all_products(retries=retries, sleep_s=sleep_s, limit=limit)
        items = payload.get("items", []) or []
        synced_ids = []

        with transaction.atomic():
            for item in items:
                product = sync_product_snapshot(item)
                if product is not None:
                    synced_ids.append(product.id)
            Product.objects.exclude(id__in=synced_ids).delete()

        self.stdout.write(self.style.SUCCESS(f"Synchronized {len(synced_ids)} products from B2B feed"))

    def _fetch_all_products(self, retries, sleep_s, limit):
        last_error = None
        for attempt in range(1, retries + 1):
            try:
                items = []
                offset = 0
                total = None
                while total is None or offset < total:
                    query = urllib.parse.urlencode({"limit": limit, "offset": offset})
                    request = urllib.request.Request(
                        f"{settings.B2B_PRODUCTS_URL}?{query}",
                        headers={"X-Service-Key": settings.INTERNAL_SERVICE_KEY},
                    )
                    with urllib.request.urlopen(request, timeout=settings.B2B_TIMEOUT) as response:
                        payload = json.loads(response.read().decode())
                    chunk = payload.get("items", []) or []
                    items.extend(chunk)
                    total = int(payload.get("total") or len(items))
                    if not chunk:
                        break
                    offset += limit
                return {"items": items}
            except (urllib.error.URLError, urllib.error.HTTPError, ValueError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt < retries:
                    time.sleep(sleep_s)

        raise CommandError(f"Failed to sync B2B catalog: {last_error}")
