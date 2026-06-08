import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import jwt
from django.conf import settings
from django.test import TestCase
from django.utils import timezone as django_timezone
from rest_framework.test import APIClient

from cart_api.models import (
    Banner,
    BannerEvent,
    Cart,
    CartItem,
    Collection,
    CollectionProduct,
    Favorite,
    ProductEventInbox,
    Subscription,
)


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
    def test_add_sku_increments_quantity_if_already_in_cart(self, mock_get):
        product = self._catalog_product(active_quantity=10)
        mock_get.return_value = self._mock_response({"items": [product]})

        first = self.client.post(
            "/api/v1/cart/items",
            {"sku_id": str(self.sku_id), "quantity": 1},
            format="json",
            HTTP_AUTHORIZATION=self.auth,
        )
        self.assertEqual(first.status_code, 200)

        second = self.client.post(
            "/api/v1/cart/items",
            {"sku_id": str(self.sku_id), "quantity": 2},
            format="json",
            HTTP_AUTHORIZATION=self.auth,
        )
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.data["items"][0]["quantity"], 3)
        self.assertEqual(CartItem.objects.filter(cart__user_id=self.user_id, sku_id=self.sku_id).count(), 1)

    @patch("cart_api.views.requests.get")
    def test_get_cart_enriched_with_b2b_data(self, mock_get):
        product = self._catalog_product(active_quantity=5)
        mock_get.return_value = self._mock_response({"items": [product]})
        self.client.post(
            "/api/v1/cart/items",
            {"sku_id": str(self.sku_id), "quantity": 2},
            format="json",
            HTTP_AUTHORIZATION=self.auth,
        )

        response = self.client.get("/api/v1/cart", HTTP_AUTHORIZATION=self.auth)
        self.assertEqual(response.status_code, 200)
        item = response.data["items"][0]
        self.assertEqual(item["product_id"], str(self.product_id))
        self.assertEqual(item["product_title"], "Neo Phone X")
        self.assertEqual(item["sku_name"], "Black 256GB")
        self.assertEqual(item["unit_price"], 12999000)
        self.assertEqual(item["available_stock"], 5)
        self.assertTrue(item["available"])
        self.assertEqual(response.data["summary"]["total_amount"], 25998000)

    @patch("cart_api.views.requests.get")
    def test_unavailable_sku_shown_with_reason(self, mock_get):
        product = self._catalog_product(active_quantity=5)
        mock_get.return_value = self._mock_response({"items": [product]})
        self.client.post(
            "/api/v1/cart/items",
            {"sku_id": str(self.sku_id), "quantity": 2},
            format="json",
            HTTP_AUTHORIZATION=self.auth,
        )

        out_of_stock = self._catalog_product(active_quantity=0)
        mock_get.return_value = self._mock_response({"items": [out_of_stock]})
        response = self.client.get("/api/v1/cart", HTTP_AUTHORIZATION=self.auth)

        self.assertEqual(response.status_code, 200)
        item = response.data["items"][0]
        self.assertFalse(item["available"])
        self.assertEqual(item["unavailable_reason"], "OUT_OF_STOCK")
        self.assertEqual(item["line_total"], 0)
        self.assertEqual(response.data["summary"]["total_amount"], 0)
        self.assertTrue(response.data["summary"]["has_unavailable_items"])

    @patch("cart_api.views.requests.get")
    def test_guest_cart_merged_on_login(self, mock_get):
        session_id = uuid.uuid4()
        guest_cart = Cart.objects.create(session_id=session_id)
        CartItem.objects.create(
            cart=guest_cart,
            product_id=self.product_id,
            sku_id=self.sku_id,
            quantity=3,
        )
        user_cart = Cart.objects.create(user_id=self.user_id)
        CartItem.objects.create(
            cart=user_cart,
            product_id=self.product_id,
            sku_id=self.sku_id,
            quantity=1,
        )
        mock_get.return_value = self._mock_response({"items": [self._catalog_product(active_quantity=10)]})

        response = self.client.get(
            "/api/v1/cart",
            HTTP_AUTHORIZATION=self.auth,
            HTTP_X_SESSION_ID=str(session_id),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["items"][0]["quantity"], 3)
        self.assertFalse(Cart.objects.filter(session_id=session_id).exists())
        self.assertEqual(CartItem.objects.filter(cart__user_id=self.user_id).count(), 1)

    def test_favorites_requires_user_identity(self):
        response = self.client.get("/api/v1/favorites")
        self.assertEqual(response.status_code, 401)

    def _put_favorite(self, product_id, query=""):
        path = f"/api/v1/favorites/{product_id}/"
        if query:
            path = f"{path}?{query}"
        return self.client.put(path, HTTP_AUTHORIZATION=self.auth)

    @patch("cart_api.views.requests.get")
    def test_add_to_favorites_returns_204(self, mock_get):
        product_id = uuid.uuid4()
        mock_get.return_value = self._mock_response({"items": [self._catalog_product(product_id=product_id)]})

        response = self._put_favorite(product_id)
        self.assertEqual(response.status_code, 204)
        self.assertTrue(Favorite.objects.filter(user_id=self.user_id, product_id=product_id).exists())

    @patch("cart_api.views.requests.get")
    def test_repeat_add_returns_204_not_duplicate(self, mock_get):
        mock_get.return_value = self._mock_response({"items": [self._catalog_product()]})

        first = self._put_favorite(self.product_id)
        self.assertEqual(first.status_code, 204)

        second = self._put_favorite(self.product_id)
        self.assertEqual(second.status_code, 204)
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

        add_response = self._put_favorite(
            self.product_id,
            query=f"user_id={other_user_id}",
        )
        self.assertEqual(add_response.status_code, 204)
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

    def _post_subscribe(self, product_id, payload, query=""):
        path = f"/api/v1/favorites/{product_id}/subscribe"
        if query:
            path = f"{path}?{query}"
        return self.client.post(path, payload, format="json", HTTP_AUTHORIZATION=self.auth)

    @patch("cart_api.views.requests.get")
    def test_subscribe_returns_204_with_events(self, mock_get):
        mock_get.return_value = self._mock_response({"items": [self._catalog_product()]})
        response = self._post_subscribe(self.product_id, {"events": ["BACK_IN_STOCK", "PRICE_DROP"]})
        self.assertEqual(response.status_code, 204)
        subscription = Subscription.objects.get(user_id=self.user_id, product_id=self.product_id)
        self.assertEqual(subscription.notify_on, ["BACK_IN_STOCK", "PRICE_DROP"])

    @patch("cart_api.views.requests.get")
    def test_duplicate_subscription_returns_409(self, mock_get):
        mock_get.return_value = self._mock_response({"items": [self._catalog_product()]})
        payload = {"events": ["BACK_IN_STOCK"]}
        self.assertEqual(self._post_subscribe(self.product_id, payload).status_code, 204)

        duplicate = self._post_subscribe(self.product_id, payload)
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(duplicate.data["code"], "SUBSCRIPTION_ALREADY_EXISTS")
        self.assertEqual(Subscription.objects.filter(user_id=self.user_id, product_id=self.product_id).count(), 1)

    def test_invalid_events_returns_400(self):
        response = self._post_subscribe(self.product_id, {"events": []})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "INVALID_EVENTS")

        bad_event = self._post_subscribe(self.product_id, {"events": ["UNKNOWN_EVENT"]})
        self.assertEqual(bad_event.status_code, 400)

    @patch("cart_api.views.requests.get")
    def test_subscribe_to_unknown_product_returns_404(self, mock_get):
        unknown_id = uuid.uuid4()
        mock_get.return_value = self._mock_response({"items": []})
        response = self._post_subscribe(unknown_id, {"events": ["BACK_IN_STOCK"]})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["code"], "PRODUCT_NOT_FOUND")

    @patch("cart_api.views.requests.get")
    def test_unsubscribe_is_idempotent(self, mock_get):
        mock_get.return_value = self._mock_response({"items": [self._catalog_product()]})
        self._post_subscribe(self.product_id, {"events": ["BACK_IN_STOCK"]})

        first = self.client.delete(
            f"/api/v1/favorites/{self.product_id}/subscribe",
            HTTP_AUTHORIZATION=self.auth,
        )
        self.assertEqual(first.status_code, 204)
        self.assertFalse(Subscription.objects.filter(user_id=self.user_id, product_id=self.product_id).exists())

        second = self.client.delete(
            f"/api/v1/favorites/{self.product_id}/subscribe",
            HTTP_AUTHORIZATION=self.auth,
        )
        self.assertEqual(second.status_code, 204)

    def test_active_banners_returned_sorted_by_priority(self):
        now = django_timezone.now()
        first = Banner.objects.create(
            title="Sale",
            image_url="/sale.jpg",
            link="/sale",
            priority=10,
            is_active=True,
            placement=Banner.Placement.HOME,
        )
        second = Banner.objects.create(
            title="New",
            image_url="/new.jpg",
            link="/new",
            priority=20,
            is_active=True,
            placement=Banner.Placement.HOME,
        )
        Banner.objects.create(
            title="Inactive",
            image_url="/off.jpg",
            link="/off",
            priority=5,
            is_active=False,
            placement=Banner.Placement.HOME,
        )
        Banner.objects.create(
            title="Expired",
            image_url="/exp.jpg",
            link="/exp",
            priority=15,
            is_active=True,
            placement=Banner.Placement.HOME,
            end_at=now - timedelta(hours=1),
        )
        Banner.objects.create(
            title="Future",
            image_url="/fut.jpg",
            link="/fut",
            priority=8,
            is_active=True,
            placement=Banner.Placement.HOME,
            start_at=now + timedelta(hours=1),
        )
        Banner.objects.create(
            title="Other placement",
            image_url="/cat.jpg",
            link="/cat",
            priority=1,
            is_active=True,
            placement="catalog",
        )

        response = self.client.get("/api/v1/catalog/banners")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["total_count"], 2)
        self.assertEqual([item["id"] for item in response.data["items"]], [str(first.id), str(second.id)])
        self.assertEqual([item["priority"] for item in response.data["items"]], [10, 20])

    def test_no_active_banners_returns_200_empty(self):
        Banner.objects.create(
            title="Inactive only",
            image_url="/off.jpg",
            link="/off",
            is_active=False,
            placement=Banner.Placement.HOME,
        )

        response = self.client.get("/api/v1/catalog/banners")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["items"], [])
        self.assertEqual(response.data["total_count"], 0)

    def test_click_on_unknown_banner_returns_400(self):
        response = self.client.post(
            "/api/v1/banner-events",
            {
                "events": [
                    {
                        "banner_id": str(uuid.uuid4()),
                        "event": "click",
                        "timestamp": "2026-05-11T19:00:05Z",
                    }
                ]
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "BANNER_NOT_FOUND")

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
        self.assertEqual(BannerEvent.objects.count(), 2)

    def test_collections_list_returns_metadata_without_products(self):
        active_high = Collection.objects.create(
            title="Хиты",
            description="Топ продаж",
            cover_image_url="/hits.jpg",
            target_url="/collections/hits",
            priority=10,
            is_active=True,
        )
        Collection.objects.create(title="Черновик", is_active=False, priority=5)
        CollectionProduct.objects.create(collection=active_high, product_id=uuid.uuid4(), ordering=1)

        response = self.client.get("/api/v1/main/collections?limit=10&offset=0")
        self.assertEqual(response.status_code, 200)
        self.assertIn("metadata", response.data)
        self.assertIn("collections", response.data)
        self.assertEqual(response.data["metadata"]["total_count"], 1)
        self.assertEqual(len(response.data["collections"]), 1)
        entry = response.data["collections"][0]
        self.assertEqual(entry["title"], "Хиты")
        self.assertEqual(entry["priority"], 10)
        self.assertNotIn("items", entry)
        self.assertNotIn("products", entry)
        self.assertNotIn("skus", entry)

    @patch("cart_api.views.requests.get")
    def test_collection_products_enriched_from_b2b(self, mock_get):
        collection = Collection.objects.create(title="Новинки", is_active=True)
        product_id = uuid.uuid4()
        CollectionProduct.objects.create(collection=collection, product_id=product_id, ordering=1)
        mock_get.return_value = self._mock_response({"items": [self._catalog_product(product_id=product_id)]})

        response = self.client.get(f"/api/v1/collections/{collection.id}/products?limit=20&offset=0")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["collection_title"], "Новинки")
        self.assertEqual(len(response.data["items"]), 1)
        self.assertEqual(response.data["items"][0]["title"], "Neo Phone X")
        self.assertEqual(response.data["total_products"], 1)
        self.assertEqual(response.data["unavailable_ids"], [])

    @patch("cart_api.views.requests.get")
    def test_unavailable_products_in_unavailable_ids(self, mock_get):
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

    @patch("cart_api.views.requests.get")
    def test_all_unavailable_products_returns_empty_items(self, mock_get):
        collection = Collection.objects.create(title="Пустая витрина", is_active=True)
        missing_a = uuid.uuid4()
        missing_b = uuid.uuid4()
        CollectionProduct.objects.create(collection=collection, product_id=missing_a, ordering=1)
        CollectionProduct.objects.create(collection=collection, product_id=missing_b, ordering=2)
        mock_get.return_value = self._mock_response({"items": []})

        response = self.client.get(f"/api/v1/collections/{collection.id}/products")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["items"], [])
        self.assertEqual(set(response.data["unavailable_ids"]), {str(missing_a), str(missing_b)})

    def test_unknown_collection_returns_404(self):
        unknown_id = uuid.uuid4()
        response = self.client.get(f"/api/v1/collections/{unknown_id}/products")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["code"], "COLLECTION_NOT_FOUND")

        inactive = Collection.objects.create(title="Скрытая", is_active=False)
        inactive_response = self.client.get(f"/api/v1/collections/{inactive.id}/products")
        self.assertEqual(inactive_response.status_code, 404)

    def _product_event_payload(self, idempotency_key="evt-block-1", event="PRODUCT_BLOCKED", sku_ids=None):
        return {
            "idempotency_key": idempotency_key,
            "event": event,
            "product_id": str(self.product_id),
            "sku_ids": sku_ids if sku_ids is not None else [str(self.sku_id)],
            "reason": "moderation",
        }

    def test_product_blocked_marks_cart_items_unavailable(self):
        cart = Cart.objects.create(user_id=self.user_id)
        item = CartItem.objects.create(
            cart=cart, product_id=self.product_id, sku_id=self.sku_id, quantity=2
        )
        other_sku = uuid.uuid4()
        other_item = CartItem.objects.create(
            cart=cart, product_id=self.product_id, sku_id=other_sku, quantity=1
        )

        response = self.client.post(
            "/api/v1/events/product",
            self._product_event_payload(),
            format="json",
            HTTP_X_SERVICE_KEY=settings.INTERNAL_SERVICE_KEY,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data.get("accepted"))

        item.refresh_from_db()
        other_item.refresh_from_db()
        self.assertEqual(item.unavailable_reason, CartItem.UnavailableReason.PRODUCT_BLOCKED)
        self.assertIsNone(other_item.unavailable_reason)

    def test_idempotent_event_no_side_effects(self):
        cart = Cart.objects.create(user_id=self.user_id)
        item = CartItem.objects.create(
            cart=cart, product_id=self.product_id, sku_id=self.sku_id, quantity=1
        )
        payload = self._product_event_payload(idempotency_key="evt-dup-ord04")

        first = self.client.post(
            "/api/v1/events/product",
            payload,
            format="json",
            HTTP_X_SERVICE_KEY=settings.INTERNAL_SERVICE_KEY,
        )
        self.assertEqual(first.status_code, 200)
        item.refresh_from_db()
        self.assertEqual(item.unavailable_reason, CartItem.UnavailableReason.PRODUCT_BLOCKED)

        second = self.client.post(
            "/api/v1/events/product",
            payload,
            format="json",
            HTTP_X_SERVICE_KEY=settings.INTERNAL_SERVICE_KEY,
        )
        self.assertEqual(second.status_code, 200)
        self.assertEqual(
            ProductEventInbox.objects.filter(idempotency_key="evt-dup-ord04").count(),
            1,
        )
        item.refresh_from_db()
        self.assertEqual(item.unavailable_reason, CartItem.UnavailableReason.PRODUCT_BLOCKED)

    def test_missing_service_key_returns_401(self):
        response = self.client.post(
            "/api/v1/events/product",
            self._product_event_payload(),
            format="json",
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.data["code"], "UNAUTHORIZED")

    def test_orders_not_affected_by_product_blocked(self):
        """US-ORD-04: only cart_items.unavailable_reason changes; quantity and orders DB stay intact."""
        from django.apps import apps

        with self.assertRaises(LookupError):
            apps.get_model("cart_api", "Order")

        cart = Cart.objects.create(user_id=self.user_id)
        item = CartItem.objects.create(
            cart=cart, product_id=self.product_id, sku_id=self.sku_id, quantity=4
        )
        response = self.client.post(
            "/api/v1/events/product",
            self._product_event_payload(),
            format="json",
            HTTP_X_SERVICE_KEY=settings.INTERNAL_SERVICE_KEY,
        )
        self.assertEqual(response.status_code, 200)
        item.refresh_from_db()
        self.assertEqual(item.unavailable_reason, CartItem.UnavailableReason.PRODUCT_BLOCKED)
        self.assertEqual(item.quantity, 4)
