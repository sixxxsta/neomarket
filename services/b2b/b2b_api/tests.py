import uuid

import jwt
from django.conf import settings
from django.test import TestCase
from rest_framework.test import APIClient

from .management.commands.consume_moderation_events import Command as ModerationConsumerCommand
from .models import Category, IntegrationInbox, IntegrationOutbox, Product, SellerProfile, Sku


class B2BApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.seller_id = uuid.uuid4()
        self.other_seller_id = uuid.uuid4()
        self.headers = {'HTTP_X_SELLER_ID': str(self.seller_id)}
        self.other_headers = {'HTTP_X_SELLER_ID': str(self.other_seller_id)}
        self.service_headers = {'HTTP_X_SERVICE_KEY': 'neomarket-internal-key'}
        self.category = Category.objects.create(name='Electronics')

    def jwt_headers(self, seller_id=None):
        seller_id = seller_id or self.seller_id
        payload = {
            'sub': str(seller_id),
            'user_id': str(seller_id),
        }
        if settings.JWT_AUDIENCE:
            payload['aud'] = settings.JWT_AUDIENCE
        if settings.JWT_ISSUER:
            payload['iss'] = settings.JWT_ISSUER
        token = jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)
        return {'HTTP_AUTHORIZATION': f'Bearer {token}'}

    def create_product_via_api(self, **extra):
        payload = {
            'title': 'Demo product',
            'description': 'demo',
            'category_id': str(self.category.id),
            'images': [{'url': 'https://example.com/product.jpg', 'ordering': 0}],
            'characteristics': [{'name': 'color', 'value': 'black'}],
        }
        payload.update(extra)
        return self.client.post('/api/v1/products', payload, format='json', **self.headers)

    def create_product(self, seller_id=None, **overrides):
        seller_id = seller_id or self.seller_id
        defaults = {
            'seller_id': seller_id,
            'title': 'Product',
            'description': 'description',
            'status': Product.Status.CREATED,
            'category': self.category,
            'images': [{'url': 'https://example.com/product.jpg'}],
            'characteristics': [{'name': 'color', 'value': 'black'}],
        }
        defaults.update(overrides)
        return Product.objects.create(**defaults)

    def create_sku(self, product, **overrides):
        defaults = {
            'product': product,
            'name': 'SKU',
            'price': 1000,
            'cost_price': 700,
            'active_quantity': 5,
            'reserved_quantity': 0,
            'images': [{'url': 'https://example.com/sku.jpg'}],
            'characteristics': [{'name': 'size', 'value': 'M'}],
        }
        defaults.update(overrides)
        return Sku.objects.create(**defaults)

    def _assert_bad_request_field(self, response, field):
        self.assertEqual(response.status_code, 400)
        message = response.data.get('message', response.data)
        if isinstance(message, dict):
            self.assertIn(field, message)
            return
        self.assertIn(field, str(message).lower())

    def test_create_product_returns_201_with_created_status(self):
        response = self.create_product_via_api(title='Canonical product')

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['status'], Product.Status.CREATED)
        self.assertEqual(response.data['skus'], [])

        product = Product.objects.get(id=response.data['id'])
        self.assertEqual(product.status, Product.Status.CREATED)
        self.assertEqual(product.skus.count(), 0)

    def test_seller_id_taken_from_jwt(self):
        foreign_seller_id = uuid.uuid4()
        payload = {
            'title': 'JWT product',
            'description': 'secure',
            'category_id': str(self.category.id),
            'images': [{'url': 'https://example.com/jwt.jpg'}],
            'seller_id': str(foreign_seller_id),
        }

        response = self.client.post('/api/v1/products', payload, format='json', **self.jwt_headers())

        self.assertEqual(response.status_code, 201)
        product = Product.objects.get(id=response.data['id'])
        self.assertEqual(product.seller_id, self.seller_id)
        self.assertNotEqual(product.seller_id, foreign_seller_id)

    def test_missing_images_returns_400(self):
        response = self.client.post(
            '/api/v1/products',
            {
                'title': 'No image product',
                'description': 'missing images',
                'category_id': str(self.category.id),
            },
            format='json',
            **self.headers,
        )
        self._assert_bad_request_field(response, 'images')

    def test_missing_category_returns_400(self):
        response = self.client.post(
            '/api/v1/products',
            {
                'title': 'No category product',
                'description': 'missing category',
                'images': [{'url': 'https://example.com/no-category.jpg'}],
            },
            format='json',
            **self.headers,
        )
        self.assertEqual(response.status_code, 400)
        message = response.data.get('message', response.data)
        message_text = str(message).lower()
        if isinstance(message, dict):
            self.assertTrue('category_id' in message or 'category_name' in message or 'category' in message_text)
        else:
            self.assertIn('category', message_text)

    def test_invalid_category_id_returns_400(self):
        response = self.client.post(
            '/api/v1/products',
            {
                'title': 'Bad category product',
                'description': 'invalid category',
                'category_id': str(uuid.uuid4()),
                'images': [{'url': 'https://example.com/bad-category.jpg'}],
            },
            format='json',
            **self.headers,
        )
        self._assert_bad_request_field(response, 'category_id')

    def test_missing_image_returns_400_for_sku_create(self):
        product = self.create_product()
        response = self.client.post(
            '/api/v1/skus',
            {
                'product_id': str(product.id),
                'name': 'No image SKU',
                'price': 500,
                'cost_price': 300,
                'active_quantity': 1,
            },
            format='json',
            **self.headers,
        )
        self.assertEqual(response.status_code, 400)

    def test_first_sku_moves_product_to_on_moderation_and_second_does_not_change_state(self):
        created = self.create_product_via_api()
        self.assertEqual(created.status_code, 201)

        first_sku = self.client.post(
            '/api/v1/skus',
            {
                'product_id': created.data['id'],
                'name': 'SKU 1',
                'price': 100,
                'cost_price': 60,
                'active_quantity': 2,
                'images': [{'url': 'https://example.com/sku-1.jpg'}],
            },
            format='json',
            **self.headers,
        )
        self.assertEqual(first_sku.status_code, 201)

        product = Product.objects.get(id=created.data['id'])
        self.assertEqual(product.status, Product.Status.ON_MODERATION)
        moderation_events = IntegrationOutbox.objects.filter(aggregate_id=product.id, event_type='PRODUCT_UPDATED')
        self.assertEqual(moderation_events.count(), 1)
        self.assertEqual(moderation_events.first().payload['event_type'], 'CREATED')
        self.assertEqual(len(moderation_events.first().payload['snapshot_after']['skus']), 1)
        self.assertEqual(moderation_events.first().payload['snapshot_after']['skus'][0]['id'], str(first_sku.data['id']))

        second_sku = self.client.post(
            '/api/v1/skus',
            {
                'product_id': created.data['id'],
                'name': 'SKU 2',
                'price': 120,
                'cost_price': 80,
                'active_quantity': 1,
                'images': [{'url': 'https://example.com/sku-2.jpg'}],
            },
            format='json',
            **self.headers,
        )
        self.assertEqual(second_sku.status_code, 201)
        product.refresh_from_db()
        self.assertEqual(product.status, Product.Status.ON_MODERATION)
        self.assertEqual(IntegrationOutbox.objects.filter(aggregate_id=product.id, event_type='PRODUCT_UPDATED').count(), 1)

    def test_soft_delete_marks_product_and_keeps_deleted_in_seller_list(self):
        product = self.create_product(status=Product.Status.MODERATED)
        sku = self.create_sku(product)

        deleted = self.client.delete(f'/api/v1/products/{product.id}', **self.headers)
        self.assertEqual(deleted.status_code, 204)

        product.refresh_from_db()
        sku.refresh_from_db()
        self.assertTrue(product.deleted)
        self.assertTrue(sku.deleted)
        self.assertEqual(sku.active_quantity, 0)

        listed = self.client.get('/api/v1/products?limit=10&offset=0', **self.headers)
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.data['items'][0]['deleted'], True)

        second_delete = self.client.delete(f'/api/v1/products/{product.id}', **self.headers)
        self.assertEqual(second_delete.status_code, 400)

        deleted_events = IntegrationOutbox.objects.filter(aggregate_id=product.id)
        self.assertTrue(deleted_events.filter(event_type='PRODUCT_DELETED').exists())
        moderation_delete = deleted_events.filter(event_type='PRODUCT_UPDATED').order_by('-created_at').first()
        self.assertIsNotNone(moderation_delete)
        self.assertEqual(moderation_delete.payload['event_type'], 'DELETED')
        self.assertEqual(moderation_delete.payload['snapshot_after']['deleted'], True)
        self.assertEqual(moderation_delete.payload['snapshot_after']['skus'], [])

    def test_apply_moderation_event_hard_block_is_idempotent_and_blocks_edits(self):
        product = self.create_product(status=Product.Status.ON_MODERATION)
        self.create_sku(product)
        payload = {
            'idempotency_key': 'evt-hard-block',
            'product_id': str(product.id),
            'status': 'BLOCKED',
            'hard_block': True,
            'blocking_reason': {'title': 'Forbidden category'},
            'field_reports': [{'field': 'title', 'message': 'Not allowed'}],
        }

        response = self.client.post('/api/v1/events/moderation', payload, format='json', **self.service_headers)
        self.assertEqual(response.status_code, 200)

        product.refresh_from_db()
        self.assertEqual(product.status, Product.Status.HARD_BLOCKED)
        self.assertEqual(product.blocking_reason['title'], 'Forbidden category')

        duplicate = self.client.post('/api/v1/events/moderation', payload, format='json', **self.service_headers)
        self.assertEqual(duplicate.status_code, 200)
        self.assertEqual(IntegrationInbox.objects.filter(message_id='evt-hard-block').count(), 1)

        edit = self.client.put(
            f'/api/v1/products/{product.id}',
            {'title': 'Changed title'},
            format='json',
            **self.headers,
        )
        self.assertEqual(edit.status_code, 403)

    def test_apply_soft_block_saves_reports_and_emits_b2c_event(self):
        product = self.create_product(status=Product.Status.ON_MODERATION)
        self.create_sku(product)
        payload = {
            'idempotency_key': 'evt-soft-block',
            'product_id': str(product.id),
            'status': 'BLOCKED',
            'blocking_reason': {'title': 'Needs documents'},
            'field_reports': [{'field': 'description', 'message': 'Need more details'}],
        }

        response = self.client.post('/api/v1/events/moderation', payload, format='json', **self.service_headers)
        self.assertEqual(response.status_code, 200)

        product.refresh_from_db()
        self.assertEqual(product.status, Product.Status.BLOCKED)
        self.assertEqual(product.blocking_reason['title'], 'Needs documents')
        self.assertEqual(product.field_reports[0]['field'], 'description')

        blocked_event = IntegrationOutbox.objects.filter(aggregate_id=product.id, event_type='PRODUCT_BLOCKED').first()
        self.assertIsNotNone(blocked_event)
        self.assertEqual(blocked_event.payload['hard_block'], False)
        projection_event = IntegrationOutbox.objects.filter(aggregate_id=product.id, event_type='PRODUCT_UPDATED').order_by('-created_at').first()
        self.assertIsNotNone(projection_event)
        self.assertEqual(projection_event.payload['snapshot_after']['status'], Product.Status.BLOCKED)

    def test_apply_moderation_event_moderated_emits_projection_update(self):
        product = self.create_product(status=Product.Status.ON_MODERATION)
        self.create_sku(product)
        payload = {
            'idempotency_key': 'evt-approve',
            'product_id': str(product.id),
            'status': 'MODERATED',
        }

        response = self.client.post('/api/v1/events/moderation', payload, format='json', **self.service_headers)
        self.assertEqual(response.status_code, 200)

        product.refresh_from_db()
        self.assertEqual(product.status, Product.Status.MODERATED)
        self.assertIsNone(product.blocking_reason)
        self.assertEqual(product.field_reports, [])

        projection_event = IntegrationOutbox.objects.filter(aggregate_id=product.id, event_type='PRODUCT_UPDATED').order_by('-created_at').first()
        self.assertIsNotNone(projection_event)
        self.assertEqual(projection_event.payload['snapshot_after']['status'], Product.Status.MODERATED)

    def test_moderation_stream_consumer_applies_approved_event_to_b2b(self):
        product = self.create_product(status=Product.Status.ON_MODERATION)
        self.create_sku(product)

        command = ModerationConsumerCommand()
        command._handle_event(
            'moderation',
            'PRODUCT_APPROVED',
            {
                'idempotency_key': 'stream-approve-1',
                'product_id': str(product.id),
                'moderated_at': '2026-05-12T10:00:00Z',
            },
        )

        product.refresh_from_db()
        self.assertEqual(product.status, Product.Status.MODERATED)
        self.assertTrue(IntegrationInbox.objects.filter(message_id='stream-approve-1').exists())

    def test_reserve_unreserve_and_fulfill_are_idempotent(self):
        product = self.create_product(status=Product.Status.MODERATED)
        sku = self.create_sku(product, active_quantity=10, reserved_quantity=0)

        reserve = self.client.post(
            '/api/v1/reserve',
            {'idempotency_key': 'reserve-1', 'items': [{'sku_id': str(sku.id), 'quantity': 3}]},
            format='json',
            **self.service_headers,
        )
        self.assertEqual(reserve.status_code, 200)
        sku.refresh_from_db()
        self.assertEqual(sku.active_quantity, 7)
        self.assertEqual(sku.reserved_quantity, 3)

        duplicate_reserve = self.client.post(
            '/api/v1/reserve',
            {'idempotency_key': 'reserve-1', 'items': [{'sku_id': str(sku.id), 'quantity': 3}]},
            format='json',
            **self.service_headers,
        )
        self.assertEqual(duplicate_reserve.status_code, 200)
        sku.refresh_from_db()
        self.assertEqual(sku.active_quantity, 7)
        self.assertEqual(sku.reserved_quantity, 3)

        unreserve = self.client.post(
            '/api/v1/unreserve',
            {'idempotency_key': 'unreserve-1', 'items': [{'sku_id': str(sku.id), 'quantity': 1}]},
            format='json',
            **self.service_headers,
        )
        self.assertEqual(unreserve.status_code, 200)
        sku.refresh_from_db()
        self.assertEqual(sku.active_quantity, 8)
        self.assertEqual(sku.reserved_quantity, 2)

        fulfill = self.client.post(
            '/api/v1/fulfill',
            {'order_id': 'order-1', 'items': [{'sku_id': str(sku.id), 'quantity': 2}]},
            format='json',
            **self.service_headers,
        )
        self.assertEqual(fulfill.status_code, 200)
        sku.refresh_from_db()
        self.assertEqual(sku.active_quantity, 8)
        self.assertEqual(sku.reserved_quantity, 0)

        duplicate_fulfill = self.client.post(
            '/api/v1/fulfill',
            {'order_id': 'order-1', 'items': [{'sku_id': str(sku.id), 'quantity': 2}]},
            format='json',
            **self.service_headers,
        )
        self.assertEqual(duplicate_fulfill.status_code, 200)
        sku.refresh_from_db()
        self.assertEqual(sku.active_quantity, 8)
        self.assertEqual(sku.reserved_quantity, 0)

    def test_reserve_rolls_back_when_any_sku_has_insufficient_stock(self):
        product = self.create_product(status=Product.Status.MODERATED)
        sku_ok = self.create_sku(product, active_quantity=5)
        sku_short = self.create_sku(product, name='Short', active_quantity=1)

        response = self.client.post(
            '/api/v1/reserve',
            {
                'idempotency_key': 'reserve-conflict',
                'items': [
                    {'sku_id': str(sku_ok.id), 'quantity': 2},
                    {'sku_id': str(sku_short.id), 'quantity': 2},
                ],
            },
            format='json',
            **self.service_headers,
        )
        self.assertEqual(response.status_code, 409)
        sku_ok.refresh_from_db()
        sku_short.refresh_from_db()
        self.assertEqual(sku_ok.active_quantity, 5)
        self.assertEqual(sku_ok.reserved_quantity, 0)
        self.assertEqual(sku_short.active_quantity, 1)
        self.assertEqual(sku_short.reserved_quantity, 0)

    def test_catalog_service_mode_returns_only_visible_products_without_sensitive_fields(self):
        visible_product = self.create_product(status=Product.Status.MODERATED)
        self.create_sku(visible_product, active_quantity=3, reserved_quantity=2, cost_price=450)

        blocked_product = self.create_product(title='Blocked', status=Product.Status.BLOCKED)
        self.create_sku(blocked_product, active_quantity=3)

        hard_blocked_product = self.create_product(title='Hard blocked', status=Product.Status.HARD_BLOCKED)
        self.create_sku(hard_blocked_product, active_quantity=3)

        deleted_product = self.create_product(title='Deleted', status=Product.Status.MODERATED, deleted=True)
        self.create_sku(deleted_product, active_quantity=3)

        no_stock_product = self.create_product(title='No stock', status=Product.Status.MODERATED)
        self.create_sku(no_stock_product, active_quantity=0)

        unauthorized = self.client.get('/api/v1/products?limit=10&offset=0')
        self.assertEqual(unauthorized.status_code, 401)

        response = self.client.get('/api/v1/products?limit=10&offset=0', **self.service_headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['total'], 1)
        returned = response.data['items'][0]
        self.assertEqual(returned['id'], str(visible_product.id))
        self.assertNotIn('cost_price', returned['skus'][0])
        self.assertNotIn('reserved_quantity', returned['skus'][0])

        ids_response = self.client.get(
            f'/api/v1/products?ids={visible_product.id},{blocked_product.id},{deleted_product.id}',
            **self.service_headers,
        )
        self.assertEqual(ids_response.status_code, 200)
        self.assertEqual(ids_response.data['total'], 1)
        self.assertEqual(ids_response.data['items'][0]['id'], str(visible_product.id))

    def test_invoice_requires_moderated_owned_sku(self):
        created_product = self.create_product(status=Product.Status.CREATED)
        created_sku = self.create_sku(created_product)

        response = self.client.post(
            '/api/v1/invoices',
            {
                'warehouse_id': str(uuid.uuid4()),
                'items': [{'sku_id': str(created_sku.id), 'quantity': 2}],
            },
            format='json',
            **self.headers,
        )
        self.assertEqual(response.status_code, 400)

        foreign_product = self.create_product(seller_id=self.other_seller_id, status=Product.Status.MODERATED, title='Foreign')
        foreign_sku = self.create_sku(foreign_product)

        foreign_response = self.client.post(
            '/api/v1/invoices',
            {
                'warehouse_id': str(uuid.uuid4()),
                'items': [{'sku_id': str(foreign_sku.id), 'quantity': 1}],
            },
            format='json',
            **self.headers,
        )
        self.assertEqual(foreign_response.status_code, 403)

    def test_seller_list_ignores_query_seller_id_and_supports_status_filter(self):
        own_blocked = self.create_product(title='Own blocked', status=Product.Status.BLOCKED)
        self.create_product(title='Own created', status=Product.Status.CREATED)
        self.create_product(seller_id=self.other_seller_id, title='Foreign blocked', status=Product.Status.BLOCKED)

        response = self.client.get(
            f'/api/v1/products?limit=10&offset=0&seller_id={self.other_seller_id}&status=BLOCKED&search=blocked',
            **self.headers,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['total'], 1)
        self.assertEqual(response.data['items'][0]['id'], str(own_blocked.id))

    def test_seller_list_search_is_case_insensitive_and_deleted_items_remain_visible(self):
        deleted_product = self.create_product(title='Neo CAMERA', status=Product.Status.MODERATED, deleted=True)
        self.create_product(title='Something else', status=Product.Status.MODERATED)

        response = self.client.get('/api/v1/products?limit=10&offset=0&search=camera', **self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['total'], 1)
        self.assertEqual(response.data['items'][0]['id'], str(deleted_product.id))
        self.assertEqual(response.data['items'][0]['deleted'], True)

    def test_delete_last_sku_on_moderation_returns_product_to_created(self):
        product = self.create_product(status=Product.Status.ON_MODERATION)
        sku = self.create_sku(product, active_quantity=2)

        response = self.client.delete(f'/api/v1/skus/{sku.id}', **self.headers)
        self.assertEqual(response.status_code, 204)

        product.refresh_from_db()
        sku.refresh_from_db()
        self.assertEqual(product.status, Product.Status.CREATED)
        self.assertTrue(sku.deleted)

        event = IntegrationOutbox.objects.filter(aggregate_id=product.id, event_type='PRODUCT_UPDATED').order_by('-created_at').first()
        self.assertIsNotNone(event)
        self.assertEqual(event.payload['event_type'], 'DELETED')

    def test_reserve_requires_service_key(self):
        product = self.create_product(status=Product.Status.MODERATED)
        sku = self.create_sku(product, active_quantity=4)

        response = self.client.post(
            '/api/v1/reserve',
            {'idempotency_key': 'reserve-no-key', 'items': [{'sku_id': str(sku.id), 'quantity': 1}]},
            format='json',
        )
        self.assertEqual(response.status_code, 401)

    def test_invoice_accept_increases_stock(self):
        product = self.create_product(status=Product.Status.MODERATED)
        sku = self.create_sku(product, active_quantity=1)

        invoice = self.client.post(
            '/api/v1/invoices',
            {
                'warehouse_id': str(uuid.uuid4()),
                'items': [{'sku_id': str(sku.id), 'quantity': 4}],
            },
            format='json',
            **self.headers,
        )
        self.assertEqual(invoice.status_code, 201)

        accepted = self.client.post(
            '/api/v1/invoices/accept',
            {'invoice_id': invoice.data['id']},
            format='json',
            **self.headers,
        )
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.data['status'], 'ACCEPTED')
        sku.refresh_from_db()
        self.assertEqual(sku.active_quantity, 5)

    def test_dashboard_endpoints_return_seller_metrics(self):
        product = self.create_product(status=Product.Status.CREATED)
        sku = self.create_sku(product, active_quantity=3)
        self.assertIsNotNone(sku.id)

        overview = self.client.get('/api/v1/dashboard/overview', **self.headers)
        self.assertEqual(overview.status_code, 200)
        self.assertEqual(overview.data['total_products'], 1)
        self.assertEqual(overview.data['total_skus'], 1)
        self.assertEqual(overview.data['total_stock'], 3)
        self.assertEqual(overview.data['created_products'], 1)

        stats = self.client.get('/api/v1/dashboard/stats', **self.headers)
        self.assertEqual(stats.status_code, 200)
        self.assertEqual(len(stats.data['recent_products']), 1)
        self.assertEqual(len(stats.data['low_stock_skus']), 1)
        self.assertEqual(stats.data['low_stock_skus'][0]['product_title'], 'Product')

    def test_profile_roundtrip_persists_seller_settings(self):
        initial = self.client.get('/api/v1/profile', **self.headers)
        self.assertEqual(initial.status_code, 200)
        self.assertEqual(initial.data['seller_id'], str(self.seller_id))

        updated = self.client.patch(
            '/api/v1/profile',
            {
                'company_name': 'NeoMarket Electronics',
                'contact_person': 'Ирина Петрова',
                'email': 'seller@example.com',
                'phone': '+79990000000',
                'warehouse_id': str(uuid.uuid4()),
            },
            format='json',
            **self.headers,
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.data['company_name'], 'NeoMarket Electronics')
        self.assertEqual(updated.data['contact_person'], 'Ирина Петрова')

        fetched = self.client.get('/api/v1/profile', **self.headers)
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(fetched.data['email'], 'seller@example.com')
        self.assertTrue(SellerProfile.objects.filter(seller_id=self.seller_id).exists())
