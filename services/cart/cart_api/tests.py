import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import jwt
from django.conf import settings
from django.test import TestCase
from rest_framework.test import APIClient

from cart_api.models import Banner, Cart, CartItem, Collection, CollectionProduct, Favorite, ProductEventInbox, Subscription


def _jwt_for_user(user_id):
    payload = {
        "sub": str(user_id),
        "exp": datetime.now(timezone.utc) + timedelta(hours=1),
    }
    if settings.JWT_ISSUER:
        payload["iss"] = settings.JWT_ISSUER
    if settings.JWT_AUDIENCE:
        payload["aud"] = settings.JWT_AUDIENCE
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


class CartApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user_id = uuid.uuid4()
        self.auth = f"Bearer {_jwt_for_user(self.user_id)}"
        self.sku_id = uuid.uuid4()
        self.product_id = uuid.uuid4()

    def _catalog_product(self, product_id=None, sku_id=None, active_quantity=5):
        return {
            "id": str(product_id or self.product_id),
            "slug": "neo-phone-x",
            "title": "Neo Phone X",
            "description": "demo",
            "images": [{"url": "https://cdn.example.com/phone.jpg", "ordering": 0}],
            "status": "MODERATED",
            "category": {"id": str(uuid.uuid4()), "name": "Phones", "slug": "phones"},
            "characteristics": [{"name": "brand", "value": "Neo"}],
            "skus": [
                {
                    "id": str(sku_id or self.sku_id),
                    "name": "Black 256GB",
                    "price": 12999000,
                    "discount": 0,
                    "image": "https://cdn.example.com/phone.jpg",
                    "active_quantity": active_quantity,
                    "characteristics": [{"name": "color", "value": "black"}],
                }
            ],
        }

    def _mock_response(self, payload, status_code=200):
        response = Mock()
        response.status_code = status_code
        response.json.return_value = payload
        return response

    def test_cart_requires_identity(self):
        response = self.client.get("/api/v1/cart")
        self.assertEqual(response.status_code, 400)

    @patch("cart_api.views.requests.get")
    def test_add_and_get_cart_item_with_jwt(self, mock_get):
        product = self._catalog_product()
        mock_get.return_value = self._mock_response({"items": [product]})
        add_response = self.client.post(
            "/api/v1/cart/items",
            {"sku_id": str(self.sku_id), "quantity": 2},
            format="json",
            HTTP_AUTHORIZATION=self.auth,
        )
        self.assertIn(add_response.status_code, [200, 201])

        get_response = self.client.get("/api/v1/cart", HTTP_AUTHORIZATION=self.auth)
        self.assertEqual(get_response.status_code, 200)
        self.assertEqual(len(get_response.data["items"]), 1)
        self.assertEqual(get_response.data["items"][0]["quantity"], 2)
        self.assertEqual(get_response.data["items"][0]["product_id"], str(self.product_id))
        self.assertEqual(get_response.data["summary"]["total_amount"], 25998000)

    def test_favorites_requires_user_identity(self):
        response = self.client.get("/api/v1/favorites")
        self.assertEqual(response.status_code, 401)

    def _post_favorite(self, product_id, query=""):
        path = f"/api/v1/favorites/{product_id}/"
        if query:
            path = f"{path}?{query}"
        return self.client.post(path, HTTP_AUTHORIZATION=self.auth)

    @patch("cart_api.views.requests.get")
    def test_add_to_favorites_returns_201(self, mock_get):
        product_id = uuid.uuid4()
        mock_get.return_value = self._mock_response({"items": [self._catalog_product(product_id=product_id)]})

        response = self._post_favorite(product_id)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(str(response.data["product_id"]), str(product_id))
        self.assertEqual(str(response.data["user_id"]), str(self.user_id))
        self.assertTrue(Favorite.objects.filter(user_id=self.user_id, product_id=product_id).exists())

    @patch("cart_api.views.requests.get")
    def test_repeat_add_returns_200_not_duplicate(self, mock_get):
        mock_get.return_value = self._mock_response({"items": [self._catalog_product()]})

        first = self._post_favorite(self.product_id)
        self.assertEqual(first.status_code, 201)

        second = self._post_favorite(self.product_id)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(
            Favorite.objects.filter(user_id=self.user_id, product_id=self.product_id).count(),
            1,
        )

    @patch("cart_api.views.requests.get")
    def test_get_favorites_enriched_from_b2b(self, mock_get):
        visible_id = uuid.uuid4()
        Favorite.objects.create(user_id=self.user_id, product_id=visible_id)
        mock_get.return_value = self._mock_response({"items": [self._catalog_product(product_id=visible_id)]})

        response = self.client.get("/api/v1/favorites?limit=20&offset=0", HTTP_AUTHORIZATION=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["items"]), 1)
        self.assertEqual(response.data["items"][0]["product"]["title"], "Neo Phone X")
        self.assertIn("added_at", response.data["items"][0])

    @patch("cart_api.views.requests.get")
    def test_blocked_product_excluded_from_list(self, mock_get):
        visible_id = uuid.uuid4()
        blocked_id = uuid.uuid4()
        Favorite.objects.create(user_id=self.user_id, product_id=visible_id)
        Favorite.objects.create(user_id=self.user_id, product_id=blocked_id)
        mock_get.return_value = self._mock_response({"items": [self._catalog_product(product_id=visible_id)]})

        response = self.client.get("/api/v1/favorites?limit=20&offset=0", HTTP_AUTHORIZATION=self.auth)
        self.assertEqual(response.status_code, 200)
        returned_ids = {item["product"]["id"] for item in response.data["items"]}
        self.assertEqual(returned_ids, {str(visible_id)})
        self.assertEqual(response.data["total"], 1)

    @patch("cart_api.views.requests.get")
    def test_user_id_from_query_is_ignored(self, mock_get):
        other_user_id = uuid.uuid4()
        other_product_id = uuid.uuid4()
        Favorite.objects.create(user_id=other_user_id, product_id=other_product_id)
        mock_get.return_value = self._mock_response({"items": [self._catalog_product(product_id=self.product_id)]})

        add_response = self._post_favorite(
            self.product_id,
            query=f"user_id={other_user_id}",
        )
        self.assertEqual(add_response.status_code, 201)
        self.assertTrue(Favorite.objects.filter(user_id=self.user_id, product_id=self.product_id).exists())
        self.assertFalse(Favorite.objects.filter(user_id=other_user_id, product_id=self.product_id).exists())

        list_response = self.client.get(
            f"/api/v1/favorites?user_id={other_user_id}",
            HTTP_AUTHORIZATION=self.auth,
        )
        self.assertEqual(list_response.status_code, 200)
        returned_ids = {item["product"]["id"] for item in list_response.data["items"]}
        self.assertEqual(returned_ids, {str(self.product_id)})
        self.assertNotIn(str(other_product_id), returned_ids)

    @patch("cart_api.views.requests.get")
    def test_subscribe_and_unsubscribe_product(self, mock_get):
        mock_get.return_value = self._mock_response({"items": [self._catalog_product()]})
        subscribe = self.client.post(
            f"/api/v1/favorites/{self.product_id}/subscribe",
            {"notify_on": ["IN_STOCK"]},
            format="json",
            HTTP_AUTHORIZATION=self.auth,
        )
        self.assertEqual(subscribe.status_code, 204)
        self.assertTrue(Subscription.objects.filter(user_id=self.user_id, product_id=self.product_id).exists())

        unsubscribe = self.client.delete(
            f"/api/v1/favorites/{self.product_id}/subscribe",
            HTTP_AUTHORIZATION=self.auth,
        )
        self.assertEqual(unsubscribe.status_code, 204)
        self.assertFalse(Subscription.objects.filter(user_id=self.user_id, product_id=self.product_id).exists())

    def test_banner_events_accept_batch(self):
        banner_a = Banner.objects.create(title="A", image_url="/a.jpg", link="/a", priority=1)
        banner_b = Banner.objects.create(title="B", image_url="/b.jpg", link="/b", priority=2)
        response = self.client.post(
            "/api/v1/banner-events",
            {
                "events": [
                    {
                        "banner_id": str(banner_a.id),
                        "event": "impression",
                        "timestamp": "2026-05-11T19:00:00Z",
                    },
                    {
                        "banner_id": str(banner_b.id),
                        "event": "click",
                        "timestamp": "2026-05-11T19:00:05Z",
                    },
                ]
            },
            format="json",
            HTTP_AUTHORIZATION=self.auth,
        )
        self.assertEqual(response.status_code, 204)

    @patch("cart_api.views.requests.get")
    def test_collection_products_returns_unavailable_ids(self, mock_get):
        collection = Collection.objects.create(title="Новинки", is_active=True)
        visible_id = uuid.uuid4()
        hidden_id = uuid.uuid4()
        CollectionProduct.objects.create(collection=collection, product_id=visible_id, ordering=1)
        CollectionProduct.objects.create(collection=collection, product_id=hidden_id, ordering=2)
        mock_get.return_value = self._mock_response({"items": [self._catalog_product(product_id=visible_id)]})

        response = self.client.get(f"/api/v1/collections/{collection.id}/products?limit=20&offset=0")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["items"]), 1)
        self.assertEqual(response.data["unavailable_ids"], [str(hidden_id)])

    def test_product_event_marks_cart_items_unavailable_and_is_idempotent(self):
        cart = Cart.objects.create(user_id=self.user_id)
        item = CartItem.objects.create(cart=cart, product_id=self.product_id, sku_id=self.sku_id, quantity=1)
        payload = {
            "idempotency_key": "evt-1",
            "event": "PRODUCT_BLOCKED",
            "product_id": str(self.product_id),
            "sku_ids": [str(self.sku_id)],
            "reason": "moderation",
            "date": "2026-05-12T08:00:00Z",
        }

        first = self.client.post(
            "/api/v1/events/product",
            payload,
            format="json",
            HTTP_X_SERVICE_KEY=settings.INTERNAL_SERVICE_KEY,
        )
        self.assertEqual(first.status_code, 200)
        item.refresh_from_db()
        self.assertEqual(item.unavailable_reason, "PRODUCT_BLOCKED")

        second = self.client.post(
            "/api/v1/events/product",
            payload,
            format="json",
            HTTP_X_SERVICE_KEY=settings.INTERNAL_SERVICE_KEY,
        )
        self.assertEqual(second.status_code, 200)
        self.assertEqual(ProductEventInbox.objects.filter(idempotency_key="evt-1").count(), 1)
