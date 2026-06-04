import uuid

import jwt
from django.conf import settings
from django.test import TestCase
from rest_framework.test import APIClient

from .management.commands.consume_moderation_events import Command as ModerationConsumerCommand
from .models import Category, IntegrationInbox, IntegrationOutbox, Invoice, Product, SellerProfile, Sku


class B2BApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.seller_id = uuid.uuid4()
        self.other_seller_id = uuid.uuid4()
        self.headers = self.jwt_headers()
        self.other_headers = self.jwt_headers(self.other_seller_id)
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
        self.assertIsInstance(message, str)
        self.assertIn(field, message.lower())

    def test_create_product_returns_201_with_created_status(self):
        response = self.create_product_via_api(title='Canonical product')

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['status'], Product.Status.CREATED)
        self.assertEqual(response.data['skus'], [])
        self.assertEqual(str(response.data['seller_id']), str(self.seller_id))
        self.assertEqual(str(response.data['category_id']), str(self.category.id))
        self.assertTrue(response.data['slug'])
        self.assertIsNone(response.data['blocking_reason_id'])
        self.assertIsNone(response.data['moderator_comment'])
        for image in response.data['images']:
            self.assertIn('id', image)
            self.assertIn('url', image)
            self.assertIn('ordering', image)

        product = Product.objects.get(id=response.data['id'])
        self.assertEqual(product.status, Product.Status.CREATED)
        self.assertEqual(product.skus.count(), 0)

        created_event = IntegrationOutbox.objects.filter(
            aggregate_id=product.id,
            event_type='PRODUCT_CREATED',
        ).first()
        self.assertIsNotNone(created_event)

    def test_create_product_requires_jwt(self):
        response = self.client.post(
            '/api/v1/products',
            {
                'title': 'No auth product',
                'description': 'missing jwt',
                'category_id': str(self.category.id),
                'images': [{'url': 'https://example.com/no-auth.jpg'}],
            },
            format='json',
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.data['code'], 'UNAUTHORIZED')

    def test_validation_error_message_is_string(self):
        response = self.client.post(
            '/api/v1/products',
            {
                'title': 'Bad payload',
                'description': 'missing images',
                'category_id': str(self.category.id),
            },
            format='json',
            **self.headers,
        )
        self.assertEqual(response.status_code, 400)
        self.assertIsInstance(response.data['message'], str)

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
        self.assertIsInstance(message, str)
        self.assertIn('category_id', message.lower())

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

    def _post_sku(self, product_id, **extra):
        payload = {
            'product_id': str(product_id),
            'name': 'SKU',
            'price': 100,
            'cost_price': 60,
            'active_quantity': 2,
            'images': [{'url': 'https://example.com/sku.jpg'}],
        }
        payload.update(extra)
        return self.client.post('/api/v1/skus', payload, format='json', **self.headers)

    def test_missing_image_returns_400(self):
        product = self.create_product()
        response = self._post_sku(product.id, images=[])
        self.assertEqual(response.status_code, 400)
        self._assert_bad_request_field(response, 'images')

    def test_first_sku_transitions_product_to_on_moderation(self):
        created = self.create_product_via_api()
        self.assertEqual(created.status_code, 201)

        response = self._post_sku(
            created.data['id'],
            name='SKU 1',
            images=[{'url': 'https://example.com/sku-1.jpg'}],
        )
        self.assertEqual(response.status_code, 201)

        product = Product.objects.get(id=created.data['id'])
        self.assertEqual(product.status, Product.Status.ON_MODERATION)

    def test_first_sku_emits_created_event_to_moderation(self):
        created = self.create_product_via_api()
        response = self._post_sku(
            created.data['id'],
            name='SKU 1',
            price=100,
            images=[{'url': 'https://example.com/sku-1.jpg'}],
        )
        self.assertEqual(response.status_code, 201)

        product = Product.objects.get(id=created.data['id'])
        events = IntegrationOutbox.objects.filter(aggregate_id=product.id, event_type='PRODUCT_UPDATED')
        self.assertEqual(events.count(), 1)

        payload = events.first().payload
        self.assertEqual(payload['event_type'], 'CREATED')
        self.assertEqual(payload['product_id'], str(product.id))
        self.assertTrue(payload.get('idempotency_key'))
        self.assertEqual(payload['snapshot_before']['status'], Product.Status.CREATED)
        self.assertEqual(payload['snapshot_after']['status'], Product.Status.ON_MODERATION)
        self.assertEqual(len(payload['snapshot_after']['skus']), 1)
        sku_snapshot = payload['snapshot_after']['skus'][0]
        self.assertEqual(sku_snapshot['id'], str(response.data['id']))
        self.assertEqual(sku_snapshot['name'], 'SKU 1')
        self.assertEqual(sku_snapshot['price'], 100)
        self.assertEqual(sku_snapshot['images'][0]['url'], 'https://example.com/sku-1.jpg')

    def test_second_sku_no_state_change(self):
        created = self.create_product_via_api()
        first = self._post_sku(created.data['id'], name='SKU 1', images=[{'url': 'https://example.com/sku-1.jpg'}])
        self.assertEqual(first.status_code, 201)

        product = Product.objects.get(id=created.data['id'])
        self.assertEqual(product.status, Product.Status.ON_MODERATION)
        events_before = IntegrationOutbox.objects.filter(aggregate_id=product.id, event_type='PRODUCT_UPDATED').count()

        second = self._post_sku(
            created.data['id'],
            name='SKU 2',
            price=120,
            images=[{'url': 'https://example.com/sku-2.jpg'}],
        )
        self.assertEqual(second.status_code, 201)

        product.refresh_from_db()
        self.assertEqual(product.status, Product.Status.ON_MODERATION)
        self.assertEqual(
            IntegrationOutbox.objects.filter(aggregate_id=product.id, event_type='PRODUCT_UPDATED').count(),
            events_before,
        )

    def test_add_sku_to_hard_blocked_returns_403(self):
        product = self.create_product(status=Product.Status.HARD_BLOCKED)
        response = self._post_sku(product.id, name='Blocked SKU')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data['code'], 'FORBIDDEN')
        product.refresh_from_db()
        self.assertEqual(product.status, Product.Status.HARD_BLOCKED)
        self.assertEqual(product.skus.filter(deleted=False).count(), 0)
        self.assertFalse(
            IntegrationOutbox.objects.filter(aggregate_id=product.id, event_type='PRODUCT_UPDATED').exists()
        )

    def test_add_sku_to_moderated_product_returns_to_on_moderation(self):
        product = self.create_product(status=Product.Status.MODERATED)
        self.create_sku(product, name='Existing SKU')

        response = self._post_sku(
            product.id,
            name='New SKU variant',
            price=1500,
            active_quantity=3,
            images=[{'url': 'https://example.com/new-sku.jpg'}],
        )
        self.assertEqual(response.status_code, 201)

        product.refresh_from_db()
        self.assertEqual(product.status, Product.Status.ON_MODERATION)
        event = IntegrationOutbox.objects.filter(
            aggregate_id=product.id,
            event_type='PRODUCT_UPDATED',
        ).order_by('-created_at').first()
        self.assertIsNotNone(event)
        self.assertEqual(event.payload['event_type'], 'EDITED')

    def test_add_sku_to_blocked_product_returns_to_on_moderation(self):
        product = self.create_product(status=Product.Status.BLOCKED)
        self.create_sku(product, name='Blocked existing SKU')

        response = self._post_sku(
            product.id,
            name='Recovery SKU',
            images=[{'url': 'https://example.com/recovery-sku.jpg'}],
        )
        self.assertEqual(response.status_code, 201)

        product.refresh_from_db()
        self.assertEqual(product.status, Product.Status.ON_MODERATION)

    def test_create_sku_response_matches_sku_response_contract(self):
        created = self.create_product_via_api()
        response = self._post_sku(
            created.data['id'],
            name='Contract SKU',
            price=999,
            cost_price=500,
            active_quantity=4,
            characteristics=[{'name': 'article', 'value': 'ART-001'}],
            images=[{'url': 'https://example.com/contract-sku.jpg'}],
        )
        self.assertEqual(response.status_code, 201)
        for field in (
            'id',
            'product_id',
            'name',
            'price',
            'discount',
            'cost_price',
            'stock_quantity',
            'active_quantity',
            'reserved_quantity',
            'article',
            'images',
            'characteristics',
            'created_at',
            'updated_at',
        ):
            self.assertIn(field, response.data)
        self.assertEqual(response.data['discount'], 0)
        self.assertEqual(response.data['stock_quantity'], 4)
        self.assertEqual(response.data['article'], 'ART-001')

    def _put_product(self, product_id, **extra):
        payload = {'title': 'Updated title'}
        payload.update(extra)
        return self.client.put(f'/api/v1/products/{product_id}', payload, format='json', **self.headers)

    def _put_sku(self, sku_id, **extra):
        payload = {'name': 'Updated SKU'}
        payload.update(extra)
        return self.client.put(f'/api/v1/skus/{sku_id}', payload, format='json', **self.headers)

    def _latest_moderation_event(self, product_id):
        return (
            IntegrationOutbox.objects.filter(aggregate_id=product_id, event_type='PRODUCT_UPDATED')
            .order_by('-created_at')
            .first()
        )

    def test_edit_moderated_product_returns_to_on_moderation(self):
        product = self.create_product(status=Product.Status.MODERATED, title='Before edit')
        self.create_sku(product)

        response = self._put_product(product.id, title='After edit')
        self.assertEqual(response.status_code, 200)

        product.refresh_from_db()
        self.assertEqual(product.status, Product.Status.ON_MODERATION)
        self.assertEqual(product.title, 'After edit')

        event = self._latest_moderation_event(product.id)
        self.assertIsNotNone(event)
        self.assertEqual(event.payload['event_type'], 'EDITED')
        self.assertEqual(event.payload['snapshot_before']['status'], Product.Status.MODERATED)
        self.assertEqual(event.payload['snapshot_after']['status'], Product.Status.ON_MODERATION)

    def test_edit_blocked_product_returns_to_on_moderation(self):
        product = self.create_product(status=Product.Status.BLOCKED, title='Blocked item')
        sku = self.create_sku(product)
        product.blocking_reason = {'title': 'Needs fix'}
        product.field_reports = [{'field': 'images', 'message': 'Bad photo'}]
        product.save(update_fields=['blocking_reason', 'field_reports'])

        response = self._put_product(product.id, description='Fixed description')
        self.assertEqual(response.status_code, 200)

        product.refresh_from_db()
        self.assertEqual(product.status, Product.Status.ON_MODERATION)
        self.assertIsNone(product.blocking_reason)
        self.assertEqual(product.field_reports, [])

        event = self._latest_moderation_event(product.id)
        self.assertEqual(event.payload['event_type'], 'EDITED')

        sku_response = self._put_sku(sku.id, price=1500)
        self.assertEqual(sku_response.status_code, 200)
        product.refresh_from_db()
        self.assertEqual(product.status, Product.Status.ON_MODERATION)

    def test_reserves_preserved_after_sku_edit(self):
        product = self.create_product(status=Product.Status.MODERATED)
        sku = self.create_sku(product, active_quantity=10, reserved_quantity=5)

        response = self._put_sku(sku.id, name='Renamed SKU', price=2000, active_quantity=10)
        self.assertEqual(response.status_code, 200)

        sku.refresh_from_db()
        self.assertEqual(sku.name, 'Renamed SKU')
        self.assertEqual(sku.price, 2000)
        self.assertEqual(sku.reserved_quantity, 5)
        self.assertEqual(sku.active_quantity, 10)

        product.refresh_from_db()
        self.assertEqual(product.status, Product.Status.ON_MODERATION)

    def test_edit_hard_blocked_returns_403(self):
        product = self.create_product(status=Product.Status.HARD_BLOCKED, title='Hard blocked')
        sku = self.create_sku(product)

        product_edit = self._put_product(product.id, title='Attempt')
        self.assertEqual(product_edit.status_code, 403)
        self.assertEqual(product_edit.data['code'], 'FORBIDDEN')

        sku_edit = self._put_sku(sku.id, name='Attempt SKU')
        self.assertEqual(sku_edit.status_code, 403)
        self.assertEqual(sku_edit.data['code'], 'FORBIDDEN')

        product.refresh_from_db()
        sku.refresh_from_db()
        self.assertEqual(product.status, Product.Status.HARD_BLOCKED)
        self.assertEqual(product.title, 'Hard blocked')
        self.assertEqual(sku.name, 'SKU')

    def test_edit_others_product_returns_403(self):
        own_product = self.create_product(title='Mine')
        foreign_product = self.create_product(
            seller_id=self.other_seller_id,
            title='Foreign',
            status=Product.Status.MODERATED,
        )
        foreign_sku = self.create_sku(foreign_product)

        own_edit = self._put_product(own_product.id, title='Still mine')
        self.assertEqual(own_edit.status_code, 200)

        foreign_product_edit = self._put_product(foreign_product.id, title='Stolen')
        self.assertEqual(foreign_product_edit.status_code, 403)
        self.assertEqual(foreign_product_edit.data['code'], 'FORBIDDEN')

        foreign_sku_edit = self.client.put(
            f'/api/v1/skus/{foreign_sku.id}',
            {'name': 'Stolen SKU'},
            format='json',
            **self.headers,
        )
        self.assertEqual(foreign_sku_edit.status_code, 403)

        foreign_product.refresh_from_db()
        foreign_sku.refresh_from_db()
        self.assertEqual(foreign_product.title, 'Foreign')
        self.assertEqual(foreign_sku.name, 'SKU')

    def test_soft_delete_marks_product_and_hides_deleted_in_seller_list(self):
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
        self.assertEqual(listed.data['total'], 0)
        self.assertEqual(listed.data['items'], [])

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

    def _create_catalog_visibility_fixtures(self):
        visible_product = self.create_product(status=Product.Status.MODERATED, title='Visible')
        visible_sku = self.create_sku(visible_product, active_quantity=3, reserved_quantity=2, cost_price=450)

        blocked_product = self.create_product(title='Blocked', status=Product.Status.BLOCKED)
        self.create_sku(blocked_product, active_quantity=3)

        hard_blocked_product = self.create_product(title='Hard blocked', status=Product.Status.HARD_BLOCKED)
        self.create_sku(hard_blocked_product, active_quantity=3)

        deleted_product = self.create_product(title='Deleted', status=Product.Status.MODERATED, deleted=True)
        self.create_sku(deleted_product, active_quantity=3)

        no_stock_product = self.create_product(title='No stock', status=Product.Status.MODERATED)
        self.create_sku(no_stock_product, active_quantity=0)

        return {
            'visible_product': visible_product,
            'visible_sku': visible_sku,
            'blocked_product': blocked_product,
            'hard_blocked_product': hard_blocked_product,
            'deleted_product': deleted_product,
            'no_stock_product': no_stock_product,
        }

    def test_catalog_returns_moderated_in_stock_products(self):
        fixtures = self._create_catalog_visibility_fixtures()

        response = self.client.get('/api/v1/products?limit=50&offset=0', **self.service_headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['total'], 1)

        item = response.data['items'][0]
        self.assertEqual(item['id'], str(fixtures['visible_product'].id))
        self.assertEqual(item['status'], Product.Status.MODERATED)
        self.assertFalse(item.get('deleted', False))
        self.assertEqual(len(item['skus']), 1)
        self.assertGreater(item['skus'][0]['active_quantity'], 0)

        returned_ids = {row['id'] for row in response.data['items']}
        self.assertNotIn(str(fixtures['blocked_product'].id), returned_ids)
        self.assertNotIn(str(fixtures['deleted_product'].id), returned_ids)
        self.assertNotIn(str(fixtures['no_stock_product'].id), returned_ids)

    def test_catalog_excludes_hard_blocked(self):
        fixtures = self._create_catalog_visibility_fixtures()

        response = self.client.get('/api/v1/products?limit=50&offset=0', **self.service_headers)
        self.assertEqual(response.status_code, 200)
        returned_ids = {row['id'] for row in response.data['items']}
        self.assertNotIn(str(fixtures['hard_blocked_product'].id), returned_ids)

    def test_catalog_missing_service_key_returns_401(self):
        self._create_catalog_visibility_fixtures()

        response = self.client.get('/api/v1/products?limit=10&offset=0')
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.data['code'], 'UNAUTHORIZED')

    def test_catalog_response_has_no_cost_price(self):
        fixtures = self._create_catalog_visibility_fixtures()

        response = self.client.get('/api/v1/products?limit=10&offset=0', **self.service_headers)
        self.assertEqual(response.status_code, 200)
        sku_payload = response.data['items'][0]['skus'][0]
        self.assertEqual(sku_payload['id'], str(fixtures['visible_sku'].id))
        self.assertNotIn('cost_price', sku_payload)
        self.assertNotIn('reserved_quantity', sku_payload)

    def test_batch_ids_returns_visible_subset(self):
        fixtures = self._create_catalog_visibility_fixtures()
        visible = fixtures['visible_product']

        response = self.client.get(
            f'/api/v1/products?ids={visible.id},{fixtures["blocked_product"].id},{fixtures["deleted_product"].id}',
            **self.service_headers,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['total'], 1)
        self.assertEqual(response.data['items'][0]['id'], str(visible.id))

    def _post_invoice(self, **extra):
        payload = {
            'warehouse_id': str(uuid.uuid4()),
            'items': [{'sku_id': str(uuid.uuid4()), 'quantity': 1}],
        }
        payload.update(extra)
        return self.client.post('/api/v1/invoices', payload, format='json', **self.headers)

    def test_create_invoice_with_moderated_sku_returns_201(self):
        product = self.create_product(status=Product.Status.MODERATED, title='Stock intake')
        sku = self.create_sku(product, active_quantity=2)
        warehouse_id = uuid.uuid4()

        response = self._post_invoice(
            warehouse_id=str(warehouse_id),
            items=[{'sku_id': str(sku.id), 'quantity': 5}],
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['status'], 'PENDING')
        self.assertEqual(str(response.data['warehouse_id']), str(warehouse_id))
        self.assertEqual(str(response.data['seller_id']), str(self.seller_id))
        self.assertEqual(len(response.data['items']), 1)
        self.assertEqual(response.data['items'][0]['sku_id'], str(sku.id))
        self.assertEqual(response.data['items'][0]['quantity'], 5)
        self.assertIsNone(response.data['accepted_at'])

        invoice = Invoice.objects.get(id=response.data['id'])
        self.assertEqual(invoice.status, Invoice.Status.PENDING)
        self.assertEqual(invoice.items.count(), 1)

    def test_empty_items_returns_400(self):
        response = self._post_invoice(items=[])
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['code'], 'BAD_REQUEST')

    def test_non_moderated_sku_returns_400(self):
        created_product = self.create_product(status=Product.Status.CREATED)
        created_sku = self.create_sku(created_product)

        response = self._post_invoice(items=[{'sku_id': str(created_sku.id), 'quantity': 2}])
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['code'], 'BAD_REQUEST')

    def test_others_sku_returns_403(self):
        foreign_product = self.create_product(
            seller_id=self.other_seller_id,
            status=Product.Status.MODERATED,
            title='Foreign',
        )
        foreign_sku = self.create_sku(foreign_product)

        response = self._post_invoice(items=[{'sku_id': str(foreign_sku.id), 'quantity': 1}])
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data['code'], 'FORBIDDEN')

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

    def test_seller_list_search_is_case_insensitive_and_excludes_deleted_items(self):
        deleted_product = self.create_product(title='Neo CAMERA', status=Product.Status.MODERATED, deleted=True)
        self.create_product(title='Something else', status=Product.Status.MODERATED)

        response = self.client.get('/api/v1/products?limit=10&offset=0&search=camera', **self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['total'], 0)
        self.assertEqual(response.data['items'], [])

    def test_delete_sets_deleted_true(self):
        product = self.create_product(status=Product.Status.MODERATED)
        self.create_sku(product, active_quantity=3)

        response = self.client.delete(f'/api/v1/products/{product.id}', **self.headers)
        self.assertEqual(response.status_code, 204)

        product.refresh_from_db()
        self.assertTrue(product.deleted)

    def test_delete_emits_event_to_moderation(self):
        product = self.create_product(status=Product.Status.MODERATED)
        self.create_sku(product, active_quantity=2)

        response = self.client.delete(f'/api/v1/products/{product.id}', **self.headers)
        self.assertEqual(response.status_code, 204)

        moderation_event = IntegrationOutbox.objects.filter(
            aggregate_id=product.id,
            event_type='PRODUCT_UPDATED',
        ).order_by('-created_at').first()
        self.assertIsNotNone(moderation_event)
        self.assertEqual(moderation_event.payload['event_type'], 'DELETED')
        self.assertEqual(moderation_event.payload['snapshot_before']['deleted'], False)
        self.assertEqual(moderation_event.payload['snapshot_after']['deleted'], True)

    def test_delete_emits_product_deleted_to_b2c(self):
        product = self.create_product(status=Product.Status.MODERATED)
        sku_1 = self.create_sku(product, name='SKU-1')
        sku_2 = self.create_sku(product, name='SKU-2')

        response = self.client.delete(f'/api/v1/products/{product.id}', **self.headers)
        self.assertEqual(response.status_code, 204)

        b2c_event = IntegrationOutbox.objects.filter(
            aggregate_id=product.id,
            event_type='PRODUCT_DELETED',
        ).order_by('-created_at').first()
        self.assertIsNotNone(b2c_event)
        self.assertEqual(b2c_event.payload['event_type'], 'DELETED')
        self.assertCountEqual(
            b2c_event.payload['sku_ids'],
            [str(sku_1.id), str(sku_2.id)],
        )

    def test_delete_already_deleted_returns_400(self):
        product = self.create_product(status=Product.Status.MODERATED, deleted=True)

        response = self.client.delete(f'/api/v1/products/{product.id}', **self.headers)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['code'], 'BAD_REQUEST')

    def test_deleted_product_not_in_seller_list(self):
        product = self.create_product(status=Product.Status.MODERATED)
        self.create_sku(product, active_quantity=4)
        self.assertEqual(self.client.delete(f'/api/v1/products/{product.id}', **self.headers).status_code, 204)

        list_response = self.client.get('/api/v1/products?limit=10&offset=0', **self.headers)
        self.assertEqual(list_response.status_code, 200)
        returned_ids = {item['id'] for item in list_response.data['items']}
        self.assertNotIn(str(product.id), returned_ids)

    def test_delete_others_product_returns_403(self):
        foreign_product = self.create_product(seller_id=self.other_seller_id, status=Product.Status.MODERATED)

        response = self.client.delete(f'/api/v1/products/{foreign_product.id}', **self.headers)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data['code'], 'FORBIDDEN')

    def test_get_moderated_product_returns_full_payload(self):
        product = self.create_product(status=Product.Status.MODERATED, title='Moderated phone')
        sku = self.create_sku(product, price=1500, cost_price=1000, reserved_quantity=2, active_quantity=8)

        response = self.client.get(f'/api/v1/products/{product.id}', **self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['id'], str(product.id))
        self.assertEqual(response.data['status'], Product.Status.MODERATED)
        self.assertEqual(response.data['title'], 'Moderated phone')
        self.assertIsNone(response.data['blocking_reason'])
        self.assertEqual(response.data['field_reports'], [])
        self.assertEqual(len(response.data['skus']), 1)
        self.assertEqual(response.data['skus'][0]['id'], str(sku.id))
        self.assertEqual(response.data['skus'][0]['cost_price'], 1000)
        self.assertEqual(response.data['skus'][0]['reserved_quantity'], 2)

    def test_get_blocked_product_returns_blocking_reason_and_field_reports(self):
        product = self.create_product(
            status=Product.Status.BLOCKED,
            blocking_reason={'title': 'Policy violation', 'id': str(uuid.uuid4())},
            field_reports=[
                {'field': 'title', 'message': 'Fix product title'},
                {'field': 'description', 'message': 'Need details'},
            ],
        )
        self.create_sku(product, active_quantity=1)

        response = self.client.get(f'/api/v1/products/{product.id}', **self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['status'], Product.Status.BLOCKED)
        self.assertEqual(response.data['blocking_reason']['title'], 'Policy violation')
        self.assertEqual(len(response.data['field_reports']), 2)
        self.assertEqual(response.data['field_reports'][0]['field'], 'title')

    def test_get_others_product_returns_404(self):
        foreign_product = self.create_product(seller_id=self.other_seller_id, status=Product.Status.MODERATED)

        response = self.client.get(f'/api/v1/products/{foreign_product.id}', **self.headers)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data['code'], 'NOT_FOUND')

    def test_get_nonexistent_returns_404(self):
        response = self.client.get(f'/api/v1/products/{uuid.uuid4()}', **self.headers)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data['code'], 'NOT_FOUND')

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
