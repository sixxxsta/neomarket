from django.test import TestCase
from rest_framework.test import APIClient


class CatalogContractTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_schema_contains_implemented_catalog_paths(self):
        response = self.client.get("/api/schema/", {"format": "json"})
        self.assertEqual(response.status_code, 200)

        paths = response.data.get("paths", {})
        expected_paths = [
            "/api/v1/products",
            "/api/v1/products/{id}",
            "/api/v1/products/{id}/similar",
            "/api/v1/products/{product_id}/skus",
            "/api/v1/products/{product_id}/skus/{sku_id}",
            "/api/v1/categories",
            "/api/v1/categories/{id}",
            "/api/v1/categories/{id}/filters",
            "/api/v1/catalog/facets",
            "/api/v1/breadcrumbs",
        ]

        for path in expected_paths:
            self.assertIn(path, paths)
