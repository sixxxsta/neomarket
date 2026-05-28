import uuid
import json
import urllib.error
from unittest.mock import patch

from django.test import TestCase
from rest_framework.test import APIClient

from catalog_api.management.commands.consume_domain_events import Command
from catalog_api.models import Category, Product, ProductAttribute, ProductImage, Sku


class CatalogEventProjectionTests(TestCase):
    def setUp(self):
        self.command = Command()
        self.category = Category.objects.create(
            id=uuid.uuid4(),
            name="Electronics",
            slug="electronics",
        )

    def test_b2b_product_blocked_event_hides_product_from_catalog_projection(self):
        product = Product.objects.create(
            id=uuid.uuid4(),
            title="Catalog Product",
            description="demo",
            status=Product.Status.MODERATED,
            category=self.category,
        )

        self.command._handle_event(
            "b2b",
            "PRODUCT_BLOCKED",
            {
                "product_id": str(product.id),
                "event_type": "PRODUCT_BLOCKED",
                "hard_block": False,
            },
        )

        product.refresh_from_db()
        self.assertEqual(product.status, Product.Status.BLOCKED)

    def test_b2b_hard_blocked_snapshot_maps_to_catalog_blocked_status(self):
        product_id = uuid.uuid4()

        self.command._handle_event(
            "b2b",
            "PRODUCT_UPDATED",
            {
                "snapshot_after": {
                    "id": str(product_id),
                    "title": "Hard blocked product",
                    "description": "demo",
                    "status": "HARD_BLOCKED",
                    "deleted": False,
                    "category": {
                        "id": str(self.category.id),
                        "name": self.category.name,
                    },
                }
            },
        )

        product = Product.objects.get(id=product_id)
        self.assertEqual(product.status, Product.Status.BLOCKED)

    def test_b2b_snapshot_projects_skus_images_and_attributes(self):
        product_id = uuid.uuid4()
        sku_id = uuid.uuid4()

        self.command._handle_event(
            "b2b",
            "PRODUCT_UPDATED",
            {
                "snapshot_after": {
                    "id": str(product_id),
                    "title": "Projected product",
                    "description": "demo",
                    "status": "MODERATED",
                    "deleted": False,
                    "category": {
                        "id": str(self.category.id),
                        "name": self.category.name,
                    },
                    "images": [{"url": "https://cdn.example.com/product.jpg", "ordering": 0}],
                    "characteristics": [{"name": "brand", "value": "Neo"}],
                    "skus": [
                        {
                            "id": str(sku_id),
                            "name": "Projected SKU",
                            "price": 199900,
                            "active_quantity": 4,
                            "images": [{"url": "https://cdn.example.com/sku.jpg", "ordering": 0}],
                            "characteristics": [{"name": "color", "value": "black"}],
                        }
                    ],
                }
            },
        )

        product = Product.objects.get(id=product_id)
        sku = Sku.objects.get(id=sku_id)
        image = ProductImage.objects.get(product=product)
        attribute = ProductAttribute.objects.get(product=product, name="brand")

        self.assertEqual(product.status, Product.Status.MODERATED)
        self.assertEqual(sku.product_id, product.id)
        self.assertEqual(sku.attributes["color"], "black")
        self.assertEqual(image.image_url, "https://cdn.example.com/product.jpg")
        self.assertEqual(attribute.value, "Neo")

    def test_direct_moderation_events_do_not_flip_catalog_status_without_b2b_snapshot(self):
        product = Product.objects.create(
            id=uuid.uuid4(),
            title="Still waiting for B2B",
            description="demo",
            status=Product.Status.BLOCKED,
            category=self.category,
        )

        self.command._handle_event(
            "moderation",
            "PRODUCT_APPROVED",
            {
                "product_id": str(product.id),
                "status": "MODERATED",
            },
        )

        product.refresh_from_db()
        self.assertEqual(product.status, Product.Status.BLOCKED)


class CatalogApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.root = Category.objects.create(name="Электроника", slug="electronics")
        self.category = Category.objects.create(name="Смартфоны", slug="smartphones", parent=self.root)
        self.product = Product.objects.create(
            title="Neo Phone X",
            description="Флагманский смартфон для тестов каталога",
            status=Product.Status.MODERATED,
            category=self.category,
        )
        ProductImage.objects.create(product=self.product, image_url="https://cdn.example.com/phone.jpg", is_main=True, order=0)
        ProductAttribute.objects.create(product=self.product, name="brand", value="Neo")
        ProductAttribute.objects.create(product=self.product, name="memory", value="256")
        Sku.objects.create(
            product=self.product,
            name="Black 256GB",
            price=12999000,
            active_quantity=7,
            attributes={"color": "black", "memory": "256"},
        )

    def test_products_search_rejects_short_query(self):
        response = self.client.get("/api/v1/products", {"search": "ab"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "INVALID_REQUEST")

    def test_product_detail_contains_slug_and_sku_shape(self):
        response = self.client.get(f"/api/v1/products/{self.product.id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["slug"], "neo-phone-x")
        self.assertEqual(response.data["images"][0]["ordering"], 0)
        self.assertEqual(response.data["skus"][0]["discount"], 0)
        self.assertEqual(response.data["skus"][0]["image"], "https://cdn.example.com/phone.jpg")

    def test_category_filters_and_facets_include_dynamic_values(self):
        filters_response = self.client.get(f"/api/v1/categories/{self.category.id}/filters")
        self.assertEqual(filters_response.status_code, 200)
        filter_slugs = {item["slug"] for item in filters_response.data["items"]}
        self.assertIn("brand", filter_slugs)
        self.assertIn("price", filter_slugs)

        with patch("urllib.request.urlopen") as mocked:
            mocked.return_value.__enter__.return_value.read.return_value = json.dumps(
                {
                    "total_count": 1,
                    "limit": 200,
                    "offset": 0,
                    "items": [
                        {
                            "id": str(self.product.id),
                            "characteristics": [{"name": "brand", "value": "Neo"}],
                            "skus": [{"characteristics": [{"name": "color", "value": "black"}]}],
                        }
                    ],
                }
            ).encode()
            facets_response = self.client.get("/api/v1/catalog/facets", {"category_id": str(self.category.id)})
        self.assertEqual(facets_response.status_code, 200)
        facet_names = {item["name"] for item in facets_response.data["facets"]}
        self.assertIn("brand", facet_names)

    def test_breadcrumbs_for_category_and_product(self):
        category_response = self.client.get("/api/v1/breadcrumbs", {"category_id": str(self.category.id)})
        self.assertEqual(category_response.status_code, 200)
        self.assertEqual(category_response.data["meta"]["resolved_via"], "category_id")
        self.assertEqual(category_response.data["data"][-1]["is_current"], True)

        product_response = self.client.get("/api/v1/breadcrumbs", {"product_id": str(self.product.id)})
        self.assertEqual(product_response.status_code, 200)
        self.assertEqual(product_response.data["meta"]["resolved_via"], "product_id")
        self.assertEqual(product_response.data["data"][-1]["name"], self.product.title)

    def test_products_ids_mode_returns_full_product_payload(self):
        with patch("urllib.request.urlopen") as mocked:
            mocked.return_value.__enter__.return_value.read.return_value = json.dumps(
                {
                    "total_count": 1,
                    "limit": 20,
                    "offset": 0,
                    "items": [{"id": str(self.product.id), "skus": [{"id": str(self.product.skus.first().id)}]}],
                }
            ).encode()
            response = self.client.get("/api/v1/products", {"ids": str(self.product.id)})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["total_count"], 1)
        self.assertEqual(response.data["items"][0]["id"], str(self.product.id))
        self.assertIn("skus", response.data["items"][0])

    def test_products_sku_ids_mode_returns_product_containing_sku(self):
        sku = self.product.skus.first()
        with patch("urllib.request.urlopen") as mocked:
            mocked.return_value.__enter__.return_value.read.return_value = json.dumps(
                {
                    "total_count": 1,
                    "limit": 20,
                    "offset": 0,
                    "items": [{"id": str(self.product.id), "skus": [{"id": str(sku.id)}]}],
                }
            ).encode()
            response = self.client.get("/api/v1/products", {"sku_ids": str(sku.id)})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["total_count"], 1)
        self.assertEqual(response.data["items"][0]["skus"][0]["id"], str(sku.id))


class CatalogFlowDoDTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.category_id = uuid.uuid4()

    def _mock_b2b_payload(self, items, total_count=None, limit=20, offset=0):
        payload = {
            "total_count": int(total_count if total_count is not None else len(items)),
            "limit": limit,
            "offset": offset,
            "items": items,
        }
        return json.dumps(payload).encode()

    def test_invalid_sort_returns_400(self):
        response = self.client.get("/api/v1/products", {"sort": "nope"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "INVALID_REQUEST")
        self.assertIn("Allowed:", response.data["message"])

    def test_b2b_unavailable_returns_502(self):
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("down")):
            response = self.client.get("/api/v1/products", {"category_id": str(self.category_id)})
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.data["code"], "B2B_UNAVAILABLE")

    def test_catalog_returns_filtered_sorted_products(self):
        items = [
            {"id": str(uuid.uuid4()), "title": "Cheaper", "min_price": 1000},
            {"id": str(uuid.uuid4()), "title": "Expensive", "min_price": 5000},
        ]
        with patch("urllib.request.urlopen") as mocked:
            mocked.return_value.__enter__.return_value.read.return_value = self._mock_b2b_payload(items)
            response = self.client.get(
                "/api/v1/products",
                {
                    "category_id": str(self.category_id),
                    "sort": "price_asc",
                    "filters[brand]": "apple",
                },
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["total_count"], 2)
        self.assertEqual(response.data["items"][0]["title"], "Cheaper")

    def test_facets_return_counts_per_filter_value(self):
        items = [
            {
                "id": str(uuid.uuid4()),
                "characteristics": [{"name": "brand", "value": "Apple"}],
                "skus": [{"characteristics": [{"name": "color", "value": "Black"}]}],
            },
            {
                "id": str(uuid.uuid4()),
                "characteristics": [{"name": "brand", "value": "Apple"}],
                "skus": [{"characteristics": [{"name": "color", "value": "White"}]}],
            },
            {
                "id": str(uuid.uuid4()),
                "characteristics": [{"name": "brand", "value": "Samsung"}],
                "skus": [{"characteristics": [{"name": "color", "value": "Black"}]}],
            },
        ]
        with patch("urllib.request.urlopen") as mocked:
            mocked.return_value.__enter__.return_value.read.return_value = self._mock_b2b_payload(
                items, total_count=3, limit=200, offset=0
            )
            response = self.client.get("/api/v1/catalog/facets", {"category_id": str(self.category_id)})
        self.assertEqual(response.status_code, 200)
        facets = {facet["name"]: facet["values"] for facet in response.data["facets"]}
        brand_counts = {item["value"]: item["count"] for item in facets["brand"]}
        self.assertEqual(brand_counts["Apple"], 2)
        self.assertEqual(brand_counts["Samsung"], 1)
