from datetime import timedelta
from uuid import uuid4

import json
import jwt
from django.conf import settings
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .models import ModerationCard, ModerationEvent
from .queue import enqueue_from_event, parse_event


def build_test_token(moderator_id=None):
    payload = {
        'sub': str(moderator_id or uuid4()),
        'roles': ['MODERATOR'],
    }
    if settings.JWT_ISSUER:
        payload['iss'] = settings.JWT_ISSUER
    if settings.JWT_AUDIENCE:
        payload['aud'] = settings.JWT_AUDIENCE
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


class ModerationApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        token = build_test_token()
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

    def test_get_next_and_approve(self):
        product_id = str(uuid4())

        enqueue = self.client.post(
            '/api/v1/product-moderation/enqueue',
            {
                'product_id': product_id,
                'event_type': 'CREATED',
                'snapshot_after': {'id': product_id, 'title': 'Demo'},
            },
            format='json',
        )
        self.assertEqual(enqueue.status_code, 201)

        next_card = self.client.post('/api/v1/product-moderation/get-next', {}, format='json')
        self.assertEqual(next_card.status_code, 200)
        self.assertEqual(next_card.data['product_id'], product_id)

        approve = self.client.post(f'/api/v1/products/{product_id}/approve', {}, format='json')
        self.assertEqual(approve.status_code, 200)
        self.assertEqual(approve.data['status'], 'MODERATED')
        event = ModerationEvent.objects.get(product_id=product_id)
        self.assertEqual(event.event_type, ModerationEvent.EventType.PRODUCT_APPROVED)
        self.assertTrue(event.payload['idempotency_key'])

    def test_parse_event_product_id_from_aggregate_id(self):
        pid = str(uuid4())
        fields = {
            b'source': b'b2b',
            b'event_type': b'PRODUCT_UPDATED',
            b'aggregate_id': pid.encode('ascii'),
            b'payload': json.dumps(
                {
                    'event_type': 'CREATED',
                    'snapshot_after': {
                        'status': 'ON_MODERATION',
                        'skus': [{'id': str(uuid4()), 'deleted': False}],
                    },
                }
            ).encode('utf-8'),
        }
        event = parse_event(fields)
        self.assertEqual(str(event['product_id']), pid)

    def test_decline_requires_reason(self):
        product_id = str(uuid4())
        ModerationCard.objects.create(
            product_id=product_id,
            event_type='UPDATED',
            snapshot_after={'id': product_id},
        )
        decline = self.client.post(f'/api/v1/products/{product_id}/decline', {'reason_code': 'UNKNOWN'}, format='json')
        self.assertEqual(decline.status_code, 400)

    def test_stream_enqueue_refreshes_existing_open_card_and_drops_non_moderation_status(self):
        product_id = str(uuid4())
        card = ModerationCard.objects.create(
            product_id=product_id,
            event_type='CREATED',
            queue_status=ModerationCard.QueueStatus.IN_REVIEW,
            assigned_to='moderator@example.com',
            snapshot_after={'id': product_id, 'title': 'Stale', 'status': 'ON_MODERATION', 'skus': [{'id': str(uuid4())}]},
        )

        refreshed = enqueue_from_event(
            {
                'source': 'b2b',
                'product_id': product_id,
                'event_type': 'UPDATED',
                'snapshot_after': {
                    'id': product_id,
                    'title': 'Fresh snapshot',
                    'status': 'ON_MODERATION',
                    'skus': [{'id': str(uuid4()), 'deleted': False}],
                },
            }
        )
        self.assertIsNotNone(refreshed)
        self.assertEqual(refreshed.id, card.id)
        refreshed.refresh_from_db()
        self.assertEqual(refreshed.queue_status, ModerationCard.QueueStatus.PENDING)
        self.assertIsNone(refreshed.assigned_to)
        self.assertEqual(refreshed.snapshot_after['title'], 'Fresh snapshot')
        self.assertEqual(ModerationCard.objects.filter(product_id=product_id).count(), 1)

        dropped = enqueue_from_event(
            {
                'source': 'b2b',
                'product_id': product_id,
                'event_type': 'UPDATED',
                'snapshot_after': {
                    'id': product_id,
                    'title': 'Approved already',
                    'status': 'MODERATED',
                    'skus': [{'id': str(uuid4()), 'deleted': False}],
                },
            }
        )
        self.assertIsNone(dropped)
        self.assertFalse(ModerationCard.objects.filter(product_id=product_id).exists())

    def test_approve_closes_all_open_duplicates_for_same_product(self):
        product_id = str(uuid4())
        ModerationCard.objects.create(product_id=product_id, event_type='CREATED', snapshot_after={'id': product_id, 'status': 'ON_MODERATION', 'skus': [{'id': str(uuid4())}]})
        ModerationCard.objects.create(product_id=product_id, event_type='UPDATED', snapshot_after={'id': product_id, 'status': 'ON_MODERATION', 'skus': [{'id': str(uuid4())}]})

        approve = self.client.post(f'/api/v1/products/{product_id}/approve', {}, format='json')
        self.assertEqual(approve.status_code, 200)
        self.assertEqual(
            ModerationCard.objects.filter(product_id=product_id, queue_status=ModerationCard.QueueStatus.APPROVED).count(),
            2,
        )


class ProductEventsApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.product_id = uuid4()
        self.service_key = settings.INTERNAL_SERVICE_KEY

    def _snapshot(self, title='Demo', status='ON_MODERATION'):
        return {
            'id': str(self.product_id),
            'title': title,
            'status': status,
            'skus': [{'id': str(uuid4()), 'deleted': False}],
        }

    def _event_payload(self, event_type='CREATED', idempotency_key=None, **extra):
        payload = {
            'idempotency_key': idempotency_key or str(uuid4()),
            'product_id': str(self.product_id),
            'event_type': event_type,
            'snapshot_after': self._snapshot(),
        }
        payload.update(extra)
        return payload

    def _post_event(self, payload, service_key=None):
        headers = {}
        if service_key is not None:
            headers['HTTP_X_SERVICE_KEY'] = service_key
        return self.client.post('/api/v1/events/product', payload, format='json', **headers)

    def test_created_pending(self):
        response = self._post_event(self._event_payload('CREATED'), service_key=self.service_key)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['accepted'])

        card = ModerationCard.objects.get(product_id=self.product_id)
        self.assertEqual(card.queue_status, ModerationCard.QueueStatus.PENDING)
        self.assertEqual(card.event_type, ModerationCard.EventType.CREATED)

    def test_edited_returns_to_review(self):
        ModerationCard.objects.create(
            product_id=self.product_id,
            event_type=ModerationCard.EventType.CREATED,
            queue_status=ModerationCard.QueueStatus.APPROVED,
            snapshot_after=self._snapshot(title='Old'),
        )
        response = self._post_event(
            self._event_payload(
                'EDITED',
                snapshot_before=self._snapshot(title='Old'),
                snapshot_after=self._snapshot(title='Edited again'),
            ),
            service_key=self.service_key,
        )
        self.assertEqual(response.status_code, 200)

        pending = ModerationCard.objects.filter(
            product_id=self.product_id,
            queue_status=ModerationCard.QueueStatus.PENDING,
        )
        self.assertEqual(pending.count(), 1)
        self.assertEqual(pending.first().snapshot_after['title'], 'Edited again')

    def test_edited_updates_in_review(self):
        card = ModerationCard.objects.create(
            product_id=self.product_id,
            event_type=ModerationCard.EventType.CREATED,
            queue_status=ModerationCard.QueueStatus.IN_REVIEW,
            assigned_to='moderator@example.com',
            snapshot_after=self._snapshot(title='Before edit'),
        )
        response = self._post_event(
            self._event_payload(
                'EDITED',
                snapshot_before=self._snapshot(title='Before edit'),
                snapshot_after=self._snapshot(title='During review'),
            ),
            service_key=self.service_key,
        )
        self.assertEqual(response.status_code, 200)

        card.refresh_from_db()
        self.assertEqual(card.queue_status, ModerationCard.QueueStatus.IN_REVIEW)
        self.assertEqual(card.assigned_to, 'moderator@example.com')
        self.assertEqual(card.snapshot_after['title'], 'During review')

    def test_deleted_archived(self):
        card = ModerationCard.objects.create(
            product_id=self.product_id,
            event_type=ModerationCard.EventType.CREATED,
            queue_status=ModerationCard.QueueStatus.PENDING,
            snapshot_after=self._snapshot(),
        )
        response = self._post_event(
            {
                'idempotency_key': str(uuid4()),
                'product_id': str(self.product_id),
                'event_type': 'DELETED',
                'snapshot_after': {'id': str(self.product_id), 'deleted': True, 'skus': []},
            },
            service_key=self.service_key,
        )
        self.assertEqual(response.status_code, 200)

        card.refresh_from_db()
        self.assertEqual(card.queue_status, ModerationCard.QueueStatus.ARCHIVED)
        self.assertFalse(
            ModerationCard.objects.filter(
                product_id=self.product_id,
                queue_status__in=[
                    ModerationCard.QueueStatus.PENDING,
                    ModerationCard.QueueStatus.IN_REVIEW,
                ],
            ).exists()
        )

    def test_duplicate_event_no_side_effects(self):
        payload = self._event_payload('CREATED', idempotency_key='evt-mod-dup-1')
        first = self._post_event(payload, service_key=self.service_key)
        second = self._post_event(payload, service_key=self.service_key)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(ModerationCard.objects.filter(product_id=self.product_id).count(), 1)

    def test_missing_service_header_401(self):
        response = self._post_event(self._event_payload('CREATED'))
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.data['code'], 'UNAUTHORIZED')


class GetNextApiTests(TestCase):
    def _client_for(self, moderator_id):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {build_test_token(moderator_id)}')
        return client

    def _pending_card(self, product_id=None, priority_queue=1, created_at=None):
        product_id = product_id or uuid4()
        card = ModerationCard.objects.create(
            product_id=product_id,
            event_type=ModerationCard.EventType.CREATED,
            queue_status=ModerationCard.QueueStatus.PENDING,
            priority_queue=priority_queue,
            snapshot_after={
                'id': str(product_id),
                'title': 'Demo',
                'status': 'ON_MODERATION',
                'skus': [{'id': str(uuid4()), 'deleted': False, 'active_quantity': 1}],
            },
        )
        if created_at:
            ModerationCard.objects.filter(pk=card.pk).update(created_at=created_at)
            card.refresh_from_db()
        return card

    def test_next_returns_oldest_pending(self):
        moderator_id = uuid4()
        client = self._client_for(moderator_id)
        older_pid = uuid4()
        newer_pid = uuid4()
        self._pending_card(older_pid, created_at=timezone.now() - timedelta(hours=2))
        self._pending_card(newer_pid, created_at=timezone.now() - timedelta(hours=1))

        response = client.post('/api/v1/product-moderation/get-next', {}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['queue_status'], 'IN_REVIEW')
        self.assertEqual(response.data['product_id'], str(older_pid))

        card = ModerationCard.objects.get(product_id=older_pid)
        self.assertEqual(card.assigned_to, str(moderator_id))
        self.assertIsNotNone(card.review_started_at)

    def test_concurrent_two_moderators_get_different_cards(self):
        mod_a = uuid4()
        mod_b = uuid4()
        client_a = self._client_for(mod_a)
        client_b = self._client_for(mod_b)
        self._pending_card(uuid4())
        self._pending_card(uuid4())

        first_a = client_a.post('/api/v1/product-moderation/get-next', {}, format='json')
        first_b = client_b.post('/api/v1/product-moderation/get-next', {}, format='json')
        self.assertEqual(first_a.status_code, 200)
        self.assertEqual(first_b.status_code, 200)
        self.assertNotEqual(first_a.data['id'], first_b.data['id'])

    def test_empty_queue_returns_204(self):
        client = self._client_for(uuid4())
        response = client.post('/api/v1/product-moderation/get-next', {}, format='json')
        self.assertEqual(response.status_code, 204)

    def test_moderator_already_has_in_review_returns_409(self):
        moderator_id = uuid4()
        client = self._client_for(moderator_id)
        self._pending_card(uuid4())
        self._pending_card(uuid4())

        first = client.post('/api/v1/product-moderation/get-next', {}, format='json')
        self.assertEqual(first.status_code, 200)

        second = client.post('/api/v1/product-moderation/get-next', {}, format='json')
        self.assertEqual(second.status_code, 409)
        self.assertEqual(second.data['code'], 'MODERATOR_ALREADY_HAS_CARD')
