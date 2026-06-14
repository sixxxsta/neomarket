from django.test import TestCase
from rest_framework.test import APIClient


class ModerationContractTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_schema_contains_implemented_moderation_paths(self):
        response = self.client.get('/api/schema/', {'format': 'json'})
        self.assertEqual(response.status_code, 200)

        paths = response.data.get('paths', {})
        expected_paths = [
            '/api/v1/events/product',
            '/api/v1/product-moderation/get-next',
            '/api/v1/product-moderation/enqueue',
            '/api/v1/tickets/{ticket_id}/approve',
            '/api/v1/tickets/{ticket_id}/block',
            '/api/v1/blocking-reasons',
        ]

        for path in expected_paths:
            self.assertIn(path, paths)
