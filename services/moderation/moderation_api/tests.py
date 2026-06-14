from datetime import timedelta
from unittest.mock import patch
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import json
import jwt
from django.conf import settings
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .models import BlockingReason, ModerationCard, ModerationEvent
from .queue import enqueue_from_event, parse_event


def blocking_reason_uuid(code):
    return str(BlockingReason.objects.get(code=code).reason_uuid)


def expected_idempotency_key(card_id, action):
    return str(uuid5(NAMESPACE_URL, f'moderation:{action}:{card_id}'))


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

    @patch('moderation_api.b2b_client.post_moderation_decision', return_value=((200, {}), None))
    def test_get_next_and_approve(self, _mock_b2b):
        product_id = str(uuid4())
        sku_id = str(uuid4())

        enqueue = self.client.post(
            '/api/v1/product-moderation/enqueue',
            {
                'product_id': product_id,
                'event_type': 'CREATED',
                'snapshot_after': {
                    'id': product_id,
                    'title': 'Demo',
                    'status': 'ON_MODERATION',
                    'skus': [{'id': sku_id, 'deleted': False}],
                },
            },
            format='json',
        )
        self.assertEqual(enqueue.status_code, 201)

        next_card = self.client.post('/api/v1/product-moderation/get-next', {}, format='json')
        self.assertEqual(next_card.status_code, 200)
        self.assertEqual(next_card.data['product_id'], product_id)

        ticket_id = next_card.data['id']
        approve = self.client.post(f'/api/v1/tickets/{ticket_id}/approve', {}, format='json')
        self.assertEqual(approve.status_code, 200)
        self.assertEqual(approve.data['status'], 'APPROVED')
        event = ModerationEvent.objects.get(product_id=product_id)
        self.assertEqual(event.event_type, ModerationEvent.EventType.PRODUCT_APPROVED)
        UUID(event.payload['idempotency_key'])
        _mock_b2b.assert_called_once()

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
        moderator_id = uuid4()
        card = ModerationCard.objects.create(
            product_id=product_id,
            event_type='UPDATED',
            queue_status=ModerationCard.QueueStatus.IN_REVIEW,
            assigned_to=str(moderator_id),
            review_started_at=timezone.now(),
            snapshot_after={'id': product_id, 'status': 'ON_MODERATION', 'skus': [{'id': str(uuid4()), 'deleted': False}]},
        )
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {build_test_token(moderator_id)}')
        decline = client.post(
            f'/api/v1/tickets/{card.id}/block',
            {'blocking_reason_ids': [str(uuid4())]},
            format='json',
        )
        self.assertEqual(decline.status_code, 400)
        self.assertEqual(decline.data['code'], 'BLOCKING_REASON_NOT_FOUND')

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

    def test_approve_requires_in_review_card(self):
        product_id = str(uuid4())
        card = ModerationCard.objects.create(
            product_id=product_id,
            event_type='CREATED',
            queue_status=ModerationCard.QueueStatus.PENDING,
            snapshot_after={
                'id': product_id,
                'status': 'ON_MODERATION',
                'skus': [{'id': str(uuid4()), 'deleted': False}],
            },
        )

        approve = self.client.post(f'/api/v1/tickets/{card.id}/approve', {}, format='json')
        self.assertEqual(approve.status_code, 404)


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


class ApproveProductApiTests(TestCase):
    def _client_for(self, moderator_id):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {build_test_token(moderator_id)}')
        return client

    def _in_review_card(self, product_id=None, moderator_id=None, snapshot=None):
        product_id = product_id or uuid4()
        moderator_id = moderator_id or uuid4()
        now = timezone.now()
        card = ModerationCard.objects.create(
            product_id=product_id,
            event_type=ModerationCard.EventType.CREATED,
            queue_status=ModerationCard.QueueStatus.IN_REVIEW,
            assigned_to=str(moderator_id),
            review_started_at=now,
            snapshot_after=snapshot
            or {
                'id': str(product_id),
                'seller_id': str(uuid4()),
                'status': 'ON_MODERATION',
                'skus': [{'id': str(uuid4()), 'deleted': False}],
            },
        )
        ModerationCard.objects.filter(pk=card.pk).update(updated_at=now)
        card.refresh_from_db()
        return card, moderator_id

    @patch('moderation_api.b2b_client.post_moderation_decision', return_value=((200, {}), None))
    def test_approve_transitions_to_moderated_and_emits_event(self, mock_b2b):
        card, moderator_id = self._in_review_card()
        client = self._client_for(moderator_id)

        response = client.post(f'/api/v1/tickets/{card.id}/approve', {}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['status'], 'APPROVED')
        self.assertEqual(response.data['id'], str(card.id))
        self.assertEqual(response.data['kind'], 'CREATE')

        card.refresh_from_db()
        self.assertEqual(card.queue_status, ModerationCard.QueueStatus.APPROVED)

        event = ModerationEvent.objects.get(product_id=card.product_id)
        self.assertEqual(event.event_type, ModerationEvent.EventType.PRODUCT_APPROVED)
        self.assertTrue(event.published)
        self.assertEqual(event.payload['event_type'], 'MODERATED')
        self.assertEqual(
            event.payload['idempotency_key'],
            expected_idempotency_key(card.id, 'approve'),
        )

        mock_b2b.assert_called_once()
        payload = mock_b2b.call_args[0][0]
        self.assertEqual(payload['event_type'], 'MODERATED')
        self.assertIn('occurred_at', payload)
        self.assertEqual(payload['product_id'], str(card.product_id))

    def test_approve_others_card_returns_403(self):
        owner = uuid4()
        intruder = uuid4()
        card, _ = self._in_review_card(moderator_id=owner)
        client = self._client_for(intruder)

        response = client.post(f'/api/v1/tickets/{card.id}/approve', {}, format='json')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data['code'], 'APPROVE_NOT_ASSIGNED')
        card.refresh_from_db()
        self.assertEqual(card.queue_status, ModerationCard.QueueStatus.IN_REVIEW)

    def test_approve_after_edited_returns_409(self):
        card, moderator_id = self._in_review_card()
        client = self._client_for(moderator_id)

        enqueue_from_event(
            {
                'source': 'b2b',
                'product_id': str(card.product_id),
                'event_type': 'EDITED',
                'snapshot_after': {
                    'id': str(card.product_id),
                    'status': 'ON_MODERATION',
                    'title': 'Seller changed title',
                    'skus': [{'id': str(uuid4()), 'deleted': False}],
                },
            }
        )
        card.refresh_from_db()
        self.assertGreater(card.updated_at, card.review_started_at)

        response = client.post(f'/api/v1/tickets/{card.id}/approve', {}, format='json')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['code'], 'APPROVE_AFTER_EDITED')

    def test_approve_without_sku_returns_409(self):
        product_id = uuid4()
        card, moderator_id = self._in_review_card(
            product_id=product_id,
            snapshot={
                'id': str(product_id),
                'status': 'ON_MODERATION',
                'skus': [],
            },
        )
        client = self._client_for(moderator_id)

        response = client.post(f'/api/v1/tickets/{card.id}/approve', {}, format='json')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['code'], 'APPROVE_WITHOUT_SKU')


class SoftBlockApiTests(TestCase):
    def _client_for(self, moderator_id):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {build_test_token(moderator_id)}')
        return client

    def _in_review_card(self, product_id=None, moderator_id=None):
        product_id = product_id or uuid4()
        moderator_id = moderator_id or uuid4()
        now = timezone.now()
        card = ModerationCard.objects.create(
            product_id=product_id,
            event_type=ModerationCard.EventType.CREATED,
            queue_status=ModerationCard.QueueStatus.IN_REVIEW,
            assigned_to=str(moderator_id),
            review_started_at=now,
            snapshot_after={
                'id': str(product_id),
                'seller_id': str(uuid4()),
                'status': 'ON_MODERATION',
                'skus': [{'id': str(uuid4()), 'deleted': False}],
            },
        )
        ModerationCard.objects.filter(pk=card.pk).update(updated_at=now)
        card.refresh_from_db()
        return card, moderator_id

    def _decline_payload(self, reason_code='BAD_MEDIA', **extra):
        payload = {
            'blocking_reason_ids': [blocking_reason_uuid(reason_code)],
            'comment': 'Fix photos',
            'field_reports': [
                {'field_path': 'product_images', 'message': 'Blurry photos'},
                {'field_path': 'description', 'message': 'Add size chart'},
            ],
        }
        payload.update(extra)
        return payload

    @patch('moderation_api.b2b_client.post_moderation_decision', return_value=((200, {}), None))
    def test_soft_block_transitions_to_blocked_with_field_reports(self, mock_b2b):
        card, moderator_id = self._in_review_card()
        client = self._client_for(moderator_id)

        response = client.post(
            f'/api/v1/tickets/{card.id}/block',
            self._decline_payload(),
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['status'], 'BLOCKED')
        self.assertEqual(response.data['id'], str(card.id))

        card.refresh_from_db()
        self.assertEqual(card.queue_status, ModerationCard.QueueStatus.BLOCKED)
        self.assertEqual(card.decline_reason.code, 'BAD_MEDIA')

        event = ModerationEvent.objects.get(product_id=card.product_id)
        self.assertTrue(event.published)
        self.assertEqual(event.payload['hard_block'], False)
        mock_b2b.assert_called_once()

    @patch('moderation_api.b2b_client.post_moderation_decision', return_value=((200, {}), None))
    def test_soft_block_emits_event_to_b2b(self, mock_b2b):
        card, moderator_id = self._in_review_card()
        client = self._client_for(moderator_id)

        client.post(f'/api/v1/tickets/{card.id}/block', self._decline_payload(), format='json')

        payload = mock_b2b.call_args[0][0]
        self.assertEqual(payload['event_type'], 'BLOCKED')
        self.assertFalse(payload['hard_block'])
        self.assertEqual(
            payload['idempotency_key'],
            expected_idempotency_key(card.id, 'soft-block'),
        )
        self.assertEqual(payload['blocking_reason_id'], blocking_reason_uuid('BAD_MEDIA'))
        self.assertEqual(payload['field_reports'][0]['field_name'], 'product_images')
        self.assertEqual(payload['field_reports'][0]['comment'], 'Blurry photos')

    def test_soft_block_unknown_reason_returns_400(self):
        card, moderator_id = self._in_review_card()
        client = self._client_for(moderator_id)

        response = client.post(
            f'/api/v1/tickets/{card.id}/block',
            {'blocking_reason_ids': [str(uuid4())]},
            format='json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['code'], 'BLOCKING_REASON_NOT_FOUND')

    def test_soft_block_others_card_returns_403(self):
        owner = uuid4()
        intruder = uuid4()
        card, _ = self._in_review_card(moderator_id=owner)
        client = self._client_for(intruder)

        response = client.post(
            f'/api/v1/tickets/{card.id}/block',
            self._decline_payload(),
            format='json',
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data['code'], 'SOFT_BLOCK_NOT_ASSIGNED')

    def test_soft_block_invalid_field_name_returns_400(self):
        card, moderator_id = self._in_review_card()
        client = self._client_for(moderator_id)
        payload = self._decline_payload()
        payload['field_reports'] = [{'field_path': 'unknown_field', 'message': 'Bad'}]

        response = client.post(f'/api/v1/tickets/{card.id}/block', payload, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['code'], 'INVALID_FIELD_NAME')

    def test_soft_block_soft_reason_on_decline_stays_soft(self):
        card, moderator_id = self._in_review_card()
        client = self._client_for(moderator_id)

        with patch('moderation_api.b2b_client.post_moderation_decision', return_value=((200, {}), None)):
            response = client.post(
                f'/api/v1/tickets/{card.id}/block',
                self._decline_payload(reason_code='BAD_MEDIA'),
                format='json',
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['status'], 'BLOCKED')
        card.refresh_from_db()
        self.assertEqual(card.queue_status, ModerationCard.QueueStatus.BLOCKED)


class HardBlockApiTests(TestCase):
    def _client_for(self, moderator_id):
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Bearer {build_test_token(moderator_id)}')
        return client

    def _in_review_card(self, product_id=None, moderator_id=None):
        product_id = product_id or uuid4()
        moderator_id = moderator_id or uuid4()
        now = timezone.now()
        card = ModerationCard.objects.create(
            product_id=product_id,
            event_type=ModerationCard.EventType.CREATED,
            queue_status=ModerationCard.QueueStatus.IN_REVIEW,
            assigned_to=str(moderator_id),
            review_started_at=now,
            snapshot_after={
                'id': str(product_id),
                'status': 'ON_MODERATION',
                'skus': [{'id': str(uuid4()), 'deleted': False}],
            },
        )
        ModerationCard.objects.filter(pk=card.pk).update(updated_at=now)
        card.refresh_from_db()
        return card, moderator_id

    def _hard_decline_payload(self, **extra):
        payload = {
            'blocking_reason_ids': [blocking_reason_uuid('FORBIDDEN_CONTENT')],
            'comment': 'Counterfeit listing',
            'field_reports': [{'field_path': 'title', 'message': 'Prohibited item'}],
        }
        payload.update(extra)
        return payload

    @patch('moderation_api.b2b_client.post_moderation_decision', return_value=((200, {}), None))
    def test_hard_block_transitions_to_terminal_and_emits_event(self, mock_b2b):
        card, moderator_id = self._in_review_card()
        client = self._client_for(moderator_id)

        response = client.post(
            f'/api/v1/tickets/{card.id}/block',
            self._hard_decline_payload(),
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['status'], 'HARD_BLOCKED')
        self.assertEqual(response.data['id'], str(card.id))

        card.refresh_from_db()
        self.assertEqual(card.queue_status, ModerationCard.QueueStatus.HARD_BLOCKED)
        self.assertEqual(card.snapshot_after['status'], 'HARD_BLOCKED')

        event = ModerationEvent.objects.get(product_id=card.product_id)
        self.assertTrue(event.published)
        mock_b2b.assert_called_once()

    @patch('moderation_api.b2b_client.post_moderation_decision', return_value=((200, {}), None))
    def test_hard_block_event_carries_hard_block_true(self, mock_b2b):
        card, moderator_id = self._in_review_card()
        client = self._client_for(moderator_id)

        client.post(f'/api/v1/tickets/{card.id}/block', self._hard_decline_payload(), format='json')

        payload = mock_b2b.call_args[0][0]
        self.assertEqual(payload['event_type'], 'BLOCKED')
        self.assertTrue(payload['hard_block'])
        self.assertEqual(
            payload['idempotency_key'],
            expected_idempotency_key(card.id, 'hard-block'),
        )
        self.assertEqual(payload['blocking_reason_id'], blocking_reason_uuid('FORBIDDEN_CONTENT'))

    def test_any_modify_on_hard_blocked_returns_403(self):
        card, moderator_id = self._in_review_card()
        client = self._client_for(moderator_id)

        ModerationCard.objects.filter(pk=card.pk).update(
            queue_status=ModerationCard.QueueStatus.HARD_BLOCKED,
            assigned_to=None,
            review_started_at=None,
        )

        approve = client.post(f'/api/v1/tickets/{card.id}/approve', {}, format='json')
        self.assertEqual(approve.status_code, 403)
        self.assertEqual(approve.data['code'], 'HARD_BLOCKED_TERMINAL')

        decline = client.post(
            f'/api/v1/tickets/{card.id}/block',
            self._hard_decline_payload(),
            format='json',
        )
        self.assertEqual(decline.status_code, 403)

        enqueue = client.post(
            '/api/v1/product-moderation/enqueue',
            {
                'product_id': str(card.product_id),
                'event_type': 'EDITED',
                'snapshot_after': {'id': str(card.product_id), 'status': 'ON_MODERATION', 'skus': [{'id': str(uuid4()), 'deleted': False}]},
            },
            format='json',
        )
        self.assertEqual(enqueue.status_code, 403)

    def test_edited_event_on_hard_blocked_is_ignored(self):
        product_id = uuid4()
        card = ModerationCard.objects.create(
            product_id=product_id,
            event_type=ModerationCard.EventType.CREATED,
            queue_status=ModerationCard.QueueStatus.HARD_BLOCKED,
            snapshot_after={'id': str(product_id), 'status': 'HARD_BLOCKED', 'title': 'Blocked'},
        )
        client = APIClient()
        response = client.post(
            '/api/v1/events/product',
            {
                'idempotency_key': 'evt-edited-hard-1',
                'product_id': str(product_id),
                'event_type': 'EDITED',
                'snapshot_after': {
                    'id': str(product_id),
                    'status': 'ON_MODERATION',
                    'title': 'Seller tried to edit',
                    'skus': [{'id': str(uuid4()), 'deleted': False}],
                },
            },
            format='json',
            HTTP_X_SERVICE_KEY=settings.INTERNAL_SERVICE_KEY,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['accepted'])
        self.assertNotIn('card_id', response.data)

        card.refresh_from_db()
        self.assertEqual(card.queue_status, ModerationCard.QueueStatus.HARD_BLOCKED)
        self.assertEqual(card.snapshot_after['title'], 'Blocked')

    def test_deleted_event_removes_hard_blocked(self):
        product_id = uuid4()
        ModerationCard.objects.create(
            product_id=product_id,
            event_type=ModerationCard.EventType.CREATED,
            queue_status=ModerationCard.QueueStatus.HARD_BLOCKED,
            snapshot_after={'id': str(product_id), 'status': 'HARD_BLOCKED'},
        )
        client = APIClient()
        response = client.post(
            '/api/v1/events/product',
            {
                'idempotency_key': 'evt-deleted-hard-1',
                'product_id': str(product_id),
                'event_type': 'DELETED',
                'snapshot_after': {'id': str(product_id), 'deleted': True},
            },
            format='json',
            HTTP_X_SERVICE_KEY=settings.INTERNAL_SERVICE_KEY,
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            ModerationCard.objects.filter(
                product_id=product_id,
                queue_status=ModerationCard.QueueStatus.HARD_BLOCKED,
            ).exists()
        )
        self.assertTrue(
            ModerationCard.objects.filter(
                product_id=product_id,
                queue_status=ModerationCard.QueueStatus.ARCHIVED,
            ).exists()
        )


class BlockingReasonsApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {build_test_token()}')

    def test_list_returns_active_reasons(self):
        response = self.client.get('/api/v1/blocking-reasons')
        self.assertEqual(response.status_code, 200)

        codes = {item['code'] for item in response.data}
        self.assertIn('BAD_MEDIA', codes)
        self.assertIn('FORBIDDEN_CONTENT', codes)

        bad_media = next(item for item in response.data if item['code'] == 'BAD_MEDIA')
        self.assertEqual(bad_media['title'], 'Некачественные фото')
        self.assertFalse(bad_media['hard_block'])
        self.assertTrue(bad_media['is_active'])
        self.assertIn('id', bad_media)

        forbidden = next(item for item in response.data if item['code'] == 'FORBIDDEN_CONTENT')
        self.assertTrue(forbidden['hard_block'])

    def test_inactive_reasons_not_visible(self):
        BlockingReason.objects.create(
            code='RETIRED_REASON',
            title='Снятая причина',
            is_active=False,
            hard_only=False,
        )

        response = self.client.get('/api/v1/blocking-reasons')
        self.assertEqual(response.status_code, 200)
        codes = {item['code'] for item in response.data}
        self.assertNotIn('RETIRED_REASON', codes)

    def test_list_filters_by_hard_block(self):
        soft = self.client.get('/api/v1/blocking-reasons', {'hard_block': 'false'})
        hard = self.client.get('/api/v1/blocking-reasons', {'hard_block': 'true'})

        self.assertEqual(soft.status_code, 200)
        self.assertEqual(hard.status_code, 200)
        self.assertTrue(all(not item['hard_block'] for item in soft.data))
        self.assertTrue(all(item['hard_block'] for item in hard.data))
        self.assertIn('FORBIDDEN_CONTENT', {item['code'] for item in hard.data})
        self.assertNotIn('FORBIDDEN_CONTENT', {item['code'] for item in soft.data})

    def test_referenced_reason_cannot_be_deleted(self):
        reason = BlockingReason.objects.get(code='BAD_MEDIA')
        ModerationCard.objects.create(
            product_id=uuid4(),
            event_type=ModerationCard.EventType.CREATED,
            queue_status=ModerationCard.QueueStatus.BLOCKED,
            decline_reason=reason,
            snapshot_after={'id': str(uuid4()), 'status': 'BLOCKED'},
        )

        from django.db.models.deletion import ProtectedError

        with self.assertRaises(ProtectedError):
            reason.delete()

        reason.refresh_from_db()
        self.assertTrue(reason.is_active)
