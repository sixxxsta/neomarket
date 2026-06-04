from django.test import TestCase
from rest_framework.test import APIClient


class CartContractTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_schema_contains_implemented_cart_paths(self):
        response = self.client.get("/api/schema/", {"format": "json"})
        self.assertEqual(response.status_code, 200)

        paths = response.data.get("paths", {})
        expected_paths = [
            "/api/v1/banner-events",
            "/api/v1/cart",
            "/api/v1/cart/items",
            "/api/v1/cart/items/{item_id}",
            "/api/v1/cart/validate",
            "/api/v1/favorites",
            "/api/v1/favorites/{product_id}",
            "/api/v1/favorites/{product_id}/subscribe",
            "/api/v1/home/banners",
            "/api/v1/main/collections",
            "/api/v1/collections/{collection_id}/products",
            "/api/v1/events/product",
        ]

        for path in expected_paths:
            self.assertIn(path, paths)
