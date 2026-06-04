import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import jwt
import requests
from django.conf import settings
from django.core.management import call_command
from django.test import TestCase
from rest_framework.test import APIClient

from orders_api.fulfill import attempt_fulfill
from orders_api.models import IntegrationOutbox, Order, OrderItem


def _jwt_for_user(user_id, is_admin=False):
    payload = {
        "sub": str(user_id),
        "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        "roles": ["ADMIN"] if is_admin else ["CUSTOMER"],
    }
    if settings.JWT_ISSUER:
        payload["iss"] = settings.JWT_ISSUER
    if settings.JWT_AUDIENCE:
        payload["aud"] = settings.JWT_AUDIENCE
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


class OrdersApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user_id = uuid.uuid4()
        self.auth = f"Bearer {_jwt_for_user(self.user_id)}"
        self.admin_auth = f"Bearer {_jwt_for_user(self.user_id, is_admin=True)}"
        self.sku_id = uuid.uuid4()
        self.product_id = uuid.uuid4()

    def _order_payload(self):
        return {
            "idempotency_key": str(uuid.uuid4()),
            "items": [{"sku_id": str(self.sku_id), "quantity": 1}],
            "delivery_address": "г. Екатеринбург, ул. Мира 19, кв. 42",
        }

    def _catalog_response(self):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {
            "items": [
                {
                    "id": str(self.product_id),
                    "title": "Neo Phone X",
                    "skus": [
                        {
                            "id": str(self.sku_id),
                            "name": "Black 256GB",
                            "price": 12999000,
                            "discount": 0,
                            "active_quantity": 3,
                        }
                    ],
                }
            ]
        }
        return response

    def _inventory_response(self, payload):
        response = Mock()
        response.status_code = 200
        response.json.return_value = payload
        return response

    @patch("orders_api.views.requests.get")
    @patch("orders_api.inventory_client.requests.post")
    def test_checkout_creates_paid_order_with_fixed_prices(self, mock_post, mock_get):
        mock_get.return_value = self._catalog_response()
        mock_post.return_value = self._inventory_response(
            {"reserved": True, "items": [{"sku_id": str(self.sku_id), "reserved_quantity": 1}]}
        )
        payload = self._order_payload()

        response = self.client.post(
            "/api/v1/orders",
            payload,
            format="json",
            HTTP_AUTHORIZATION=self.auth,
            HTTP_IDEMPOTENCY_KEY=payload["idempotency_key"],
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["status"], "PAID")
        self.assertEqual(response.data["items"][0]["unit_price"], 12999000)
        self.assertEqual(response.data["items"][0]["product_title"], "Neo Phone X")
        self.assertEqual(response.data["items"][0]["sku_name"], "Black 256GB")

        order_item = OrderItem.objects.get(order_id=response.data["id"])
        self.assertEqual(order_item.unit_price_amount, 12999000)
        self.assertEqual(order_item.product_title, "Neo Phone X")
        self.assertEqual(order_item.sku_name, "Black 256GB")

    @patch("orders_api.views.requests.get")
    @patch("orders_api.inventory_client.requests.post")
    def test_partial_reserve_failure_returns_409(self, mock_post, mock_get):
        mock_get.return_value = self._catalog_response()
        failed = Mock()
        failed.status_code = 409
        failed.json.return_value = {
            "code": "CONFLICT",
            "message": "Insufficient stock for reserve",
            "failed_items": [
                {
                    "sku_id": str(self.sku_id),
                    "requested": 1,
                    "available": 0,
                    "reason": "OUT_OF_STOCK",
                }
            ],
        }
        mock_post.return_value = failed

        response = self.client.post(
            "/api/v1/orders",
            self._order_payload(),
            format="json",
            HTTP_AUTHORIZATION=self.auth,
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["code"], "RESERVE_FAILED")
        self.assertEqual(len(response.data["failed_items"]), 1)
        self.assertEqual(Order.objects.count(), 0)

    @patch("orders_api.views.requests.get")
    @patch("orders_api.inventory_client.requests.post")
    def test_idempotency_returns_existing_order(self, mock_post, mock_get):
        mock_get.return_value = self._catalog_response()
        mock_post.return_value = self._inventory_response(
            {"reserved": True, "items": [{"sku_id": str(self.sku_id), "reserved_quantity": 1}]}
        )
        payload = self._order_payload()

        first = self.client.post(
            "/api/v1/orders",
            payload,
            format="json",
            HTTP_AUTHORIZATION=self.auth,
            HTTP_IDEMPOTENCY_KEY=payload["idempotency_key"],
        )
        self.assertEqual(first.status_code, 201)

        second = self.client.post(
            "/api/v1/orders",
            payload,
            format="json",
            HTTP_AUTHORIZATION=self.auth,
            HTTP_IDEMPOTENCY_KEY=payload["idempotency_key"],
        )
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.data["id"], second.data["id"])
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(mock_post.call_count, 1)

    @patch("orders_api.views.requests.get")
    @patch("orders_api.inventory_client.requests.post")
    def test_b2b_unavailable_returns_503(self, mock_post, mock_get):
        mock_get.side_effect = requests.RequestException("connection refused")

        response = self.client.post(
            "/api/v1/orders",
            self._order_payload(),
            format="json",
            HTTP_AUTHORIZATION=self.auth,
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.data["code"], "B2B_UNAVAILABLE")
        self.assertEqual(Order.objects.count(), 0)
        mock_post.assert_not_called()

    @patch("orders_api.views.requests.get")
    @patch("orders_api.inventory_client.requests.post")
    def test_invalid_status_transition_returns_409(self, mock_post, mock_get):
        mock_get.return_value = self._catalog_response()
        mock_post.return_value = self._inventory_response({"reserved": True, "items": [{"sku_id": str(self.sku_id), "reserved_quantity": 1}]})

        created = self.client.post("/api/v1/orders", self._order_payload(), format="json", HTTP_AUTHORIZATION=self.auth)
        self.assertEqual(created.status_code, 201)
        order_id = created.data["id"]

        patch_response = self.client.patch(
            f"/api/v1/orders/{order_id}/status",
            {"status": "DELIVERED"},
            format="json",
            HTTP_AUTHORIZATION=self.admin_auth,
        )
        self.assertEqual(patch_response.status_code, 409)

    @patch("orders_api.views.requests.get")
    @patch("orders_api.inventory_client.requests.post")
    def test_cancel_paid_order_transitions_to_cancelled(self, mock_post, mock_get):
        mock_get.return_value = self._catalog_response()
        mock_post.side_effect = [
            self._inventory_response({"reserved": True, "items": [{"sku_id": str(self.sku_id), "reserved_quantity": 1}]}),
            self._inventory_response({"unreserved": True, "items": [{"sku_id": str(self.sku_id), "unreserved_quantity": 1}]}),
        ]
        created = self.client.post("/api/v1/orders", self._order_payload(), format="json", HTTP_AUTHORIZATION=self.auth)
        order_id = created.data["id"]

        canceled = self.client.post(f"/api/v1/orders/{order_id}/cancel", {}, format="json", HTTP_AUTHORIZATION=self.auth)
        self.assertEqual(canceled.status_code, 200)
        self.assertEqual(canceled.data["status"], "CANCELLED")
        self.assertEqual(Order.objects.get(id=order_id).status, Order.Status.CANCELED)

    @patch("orders_api.views.requests.get")
    @patch("orders_api.inventory_client.requests.post")
    def test_unreserve_failure_transitions_to_cancel_pending(self, mock_post, mock_get):
        mock_get.return_value = self._catalog_response()
        mock_post.side_effect = [
            self._inventory_response({"reserved": True, "items": [{"sku_id": str(self.sku_id), "reserved_quantity": 1}]}),
            requests.RequestException("connection refused"),
        ]
        created = self.client.post("/api/v1/orders", self._order_payload(), format="json", HTTP_AUTHORIZATION=self.auth)
        order_id = created.data["id"]

        canceled = self.client.post(f"/api/v1/orders/{order_id}/cancel", {}, format="json", HTTP_AUTHORIZATION=self.auth)
        self.assertEqual(canceled.status_code, 200)
        self.assertEqual(canceled.data["status"], "CANCEL_PENDING")
        self.assertEqual(Order.objects.get(id=order_id).status, Order.Status.CANCEL_PENDING)

    @patch("orders_api.views.requests.get")
    @patch("orders_api.inventory_client.requests.post")
    def test_cancel_assembling_order_returns_409(self, mock_post, mock_get):
        created = self._create_order(mock_post, mock_get)
        order_id = created.data["id"]
        order = Order.objects.get(id=order_id)
        order.status = Order.Status.ASSEMBLING
        order.save(update_fields=["status"])

        response = self.client.post(f"/api/v1/orders/{order_id}/cancel", {}, format="json", HTTP_AUTHORIZATION=self.auth)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data["code"], "CANCEL_NOT_ALLOWED")
        self.assertEqual(response.data["current_status"], "ASSEMBLING")
        self.assertEqual(Order.objects.get(id=order_id).status, Order.Status.ASSEMBLING)

    @patch("orders_api.views.requests.get")
    @patch("orders_api.inventory_client.requests.post")
    def test_other_user_order_returns_404(self, mock_post, mock_get):
        created = self._create_order(mock_post, mock_get)
        order_id = created.data["id"]
        other_auth = f"Bearer {_jwt_for_user(uuid.uuid4())}"

        response = self.client.post(f"/api/v1/orders/{order_id}/cancel", {}, format="json", HTTP_AUTHORIZATION=other_auth)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["code"], "ORDER_NOT_FOUND")
        self.assertNotEqual(response.status_code, 403)

    def _transition_to_delivered(self, order_id):
        order = Order.objects.get(id=order_id)
        order.status = Order.Status.ASSEMBLING
        order.save(update_fields=["status"])
        self.client.patch(
            f"/api/v1/orders/{order_id}/status",
            {"status": "DELIVERING"},
            format="json",
            HTTP_AUTHORIZATION=self.admin_auth,
        )
        return self.client.patch(
            f"/api/v1/orders/{order_id}/status",
            {"status": "DELIVERED"},
            format="json",
            HTTP_AUTHORIZATION=self.admin_auth,
        )

    @patch("orders_api.views.requests.get")
    @patch("orders_api.inventory_client.requests.post")
    def test_delivered_status_triggers_fulfill_to_b2b(self, mock_post, mock_get):
        mock_get.return_value = self._catalog_response()
        mock_post.side_effect = [
            self._inventory_response({"reserved": True, "items": [{"sku_id": str(self.sku_id), "reserved_quantity": 1}]}),
            self._inventory_response({"fulfilled": True, "items": [{"sku_id": str(self.sku_id), "reserved_quantity": 0}]}),
        ]
        created = self.client.post("/api/v1/orders", self._order_payload(), format="json", HTTP_AUTHORIZATION=self.auth)
        order_id = created.data["id"]

        delivered = self._transition_to_delivered(order_id)
        self.assertEqual(delivered.status_code, 200)
        self.assertEqual(delivered.data["status"], "DELIVERED")

        fulfill_call = mock_post.call_args_list[-1]
        self.assertEqual(fulfill_call.args[0], settings.B2B_FULFILL_URL)
        self.assertEqual(fulfill_call.kwargs["json"]["order_id"], order_id)
        self.assertEqual(
            fulfill_call.kwargs["json"]["items"],
            [{"sku_id": str(self.sku_id), "quantity": 1}],
        )
        order = Order.objects.get(id=order_id)
        self.assertIsNotNone(order.fulfilled_at)

    @patch("orders_api.views.requests.get")
    @patch("orders_api.inventory_client.requests.post")
    def test_fulfill_failure_retried_asynchronously(self, mock_post, mock_get):
        mock_get.return_value = self._catalog_response()
        mock_post.side_effect = [
            self._inventory_response({"reserved": True, "items": [{"sku_id": str(self.sku_id), "reserved_quantity": 1}]}),
            requests.RequestException("connection refused"),
        ]
        created = self.client.post("/api/v1/orders", self._order_payload(), format="json", HTTP_AUTHORIZATION=self.auth)
        order_id = created.data["id"]

        delivered = self._transition_to_delivered(order_id)
        self.assertEqual(delivered.status_code, 200)
        self.assertEqual(Order.objects.get(id=order_id).status, Order.Status.DELIVERED)
        self.assertTrue(
            IntegrationOutbox.objects.filter(
                aggregate_id=order_id,
                event_type="ORDER_FULFILL_PENDING",
                published=False,
            ).exists()
        )

        mock_post.side_effect = [
            self._inventory_response({"fulfilled": True, "items": [{"sku_id": str(self.sku_id), "reserved_quantity": 0}]}),
        ]
        call_command("retry_pending_fulfill")
        order = Order.objects.get(id=order_id)
        self.assertIsNotNone(order.fulfilled_at)
        self.assertFalse(
            IntegrationOutbox.objects.filter(
                aggregate_id=order_id,
                event_type="ORDER_FULFILL_PENDING",
                published=False,
            ).exists()
        )

    @patch("orders_api.inventory_client.requests.post")
    def test_repeated_fulfill_idempotent(self, mock_post):
        mock_post.return_value = self._inventory_response(
            {"fulfilled": True, "items": [{"sku_id": str(self.sku_id), "reserved_quantity": 0}]}
        )
        order = Order.objects.create(
            user_id=self.user_id,
            status=Order.Status.DELIVERED,
            total_amount=12999000,
            payment_method=Order.PaymentMethod.CARD_ONLINE,
            delivery_address={"city": "Ekb"},
        )
        OrderItem.objects.create(
            order=order,
            product_id=self.product_id,
            sku_id=self.sku_id,
            quantity=1,
            unit_price_amount=12999000,
            line_total_amount=12999000,
        )

        self.assertTrue(attempt_fulfill(order)[0])
        order.refresh_from_db()
        self.assertTrue(attempt_fulfill(order)[0])
        self.assertEqual(mock_post.call_count, 1)

    def _create_order(self, mock_post, mock_get, user_auth=None):
        mock_get.return_value = self._catalog_response()
        mock_post.return_value = self._inventory_response(
            {"reserved": True, "items": [{"sku_id": str(self.sku_id), "reserved_quantity": 1}]}
        )
        payload = self._order_payload()
        return self.client.post(
            "/api/v1/orders",
            payload,
            format="json",
            HTTP_AUTHORIZATION=user_auth or self.auth,
            HTTP_IDEMPOTENCY_KEY=payload["idempotency_key"],
        )

    @patch("orders_api.views.requests.get")
    @patch("orders_api.inventory_client.requests.post")
    def test_orders_list_returns_own_orders_paginated(self, mock_post, mock_get):
        self.assertEqual(self._create_order(mock_post, mock_get).status_code, 201)
        self.assertEqual(self._create_order(mock_post, mock_get).status_code, 201)

        other_user_id = uuid.uuid4()
        other_auth = f"Bearer {_jwt_for_user(other_user_id)}"
        self.assertEqual(self._create_order(mock_post, mock_get, user_auth=other_auth).status_code, 201)

        page_one = self.client.get("/api/v1/orders?limit=1&offset=0", HTTP_AUTHORIZATION=self.auth)
        self.assertEqual(page_one.status_code, 200)
        self.assertEqual(len(page_one.data["items"]), 1)
        self.assertEqual(page_one.data["total_count"], 2)
        self.assertEqual(page_one.data["total"], 2)

        page_two = self.client.get("/api/v1/orders?limit=1&offset=1", HTTP_AUTHORIZATION=self.auth)
        self.assertEqual(page_two.status_code, 200)
        self.assertEqual(len(page_two.data["items"]), 1)
        self.assertNotEqual(page_one.data["items"][0]["id"], page_two.data["items"][0]["id"])

        paid_only = self.client.get("/api/v1/orders?status=PAID", HTTP_AUTHORIZATION=self.auth)
        self.assertEqual(paid_only.status_code, 200)
        self.assertEqual(paid_only.data["total_count"], 2)

        spoofed = self.client.get(
            f"/api/v1/orders?user_id={other_user_id}",
            HTTP_AUTHORIZATION=self.auth,
        )
        self.assertEqual(spoofed.status_code, 200)
        self.assertEqual(spoofed.data["total_count"], 2)

    @patch("orders_api.views.requests.get")
    @patch("orders_api.inventory_client.requests.post")
    def test_order_detail_shows_fixed_prices(self, mock_post, mock_get):
        created = self._create_order(mock_post, mock_get)
        order_id = created.data["id"]

        changed_catalog = self._catalog_response()
        changed_catalog.json.return_value["items"][0]["skus"][0]["price"] = 99999999
        mock_get.return_value = changed_catalog

        detail = self.client.get(f"/api/v1/orders/{order_id}", HTTP_AUTHORIZATION=self.auth)
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.data["items"][0]["unit_price"], 12999000)
        self.assertEqual(mock_get.call_count, 1)

        order_item = OrderItem.objects.get(order_id=order_id)
        self.assertEqual(order_item.unit_price_amount, 12999000)

    @patch("orders_api.views.requests.get")
    @patch("orders_api.inventory_client.requests.post")
    def test_other_user_order_detail_returns_404_not_403(self, mock_post, mock_get):
        created = self._create_order(mock_post, mock_get)
        order_id = created.data["id"]
        other_auth = f"Bearer {_jwt_for_user(uuid.uuid4())}"

        response = self.client.get(f"/api/v1/orders/{order_id}", HTTP_AUTHORIZATION=other_auth)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data["code"], "ORDER_NOT_FOUND")
        self.assertNotEqual(response.status_code, 403)

    @patch("orders_api.views.requests.get")
    @patch("orders_api.inventory_client.requests.post")
    def test_orders_not_affected_by_product_blocked(self, mock_post, mock_get):
        """US-ORD-04: PRODUCT_BLOCKED is handled in cart_db; checkout snapshot in orders_db is immutable."""
        created = self._create_order(mock_post, mock_get)
        order = Order.objects.get(pk=created.data["id"])
        item = order.items.get(sku_id=self.sku_id)
        snapshot = {
            "status": order.status,
            "quantity": item.quantity,
            "unit_price_amount": item.unit_price_amount,
            "line_total_amount": item.line_total_amount,
            "product_title": item.product_title,
        }

        order.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual(order.status, snapshot["status"])
        self.assertEqual(item.quantity, snapshot["quantity"])
        self.assertEqual(item.unit_price_amount, snapshot["unit_price_amount"])
        self.assertEqual(item.line_total_amount, snapshot["line_total_amount"])
        self.assertEqual(item.product_title, snapshot["product_title"])
        self.assertFalse(hasattr(OrderItem, "unavailable_reason"))
