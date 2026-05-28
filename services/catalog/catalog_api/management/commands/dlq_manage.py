"""
Management command to inspect and manage Dead Letter Queue (DLQ) messages.
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from redis import Redis
import sys
import os
import json
from datetime import datetime

# Add parent directory to path for infra module imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../../../../'))
from infra.event_consumer_retry import EventConsumerWithRetry


class Command(BaseCommand):
    help = 'Inspect and manage Dead Letter Queue (DLQ) messages'

    def add_arguments(self, parser):
        parser.add_argument(
            'action',
            type=str,
            choices=['list', 'inspect', 'reprocess', 'clear'],
            help='Action to perform on DLQ'
        )
        parser.add_argument(
            '--message-id',
            type=str,
            help='Specific message ID to inspect or reprocess'
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=50,
            help='Number of messages to list (default: 50)'
        )

    def handle(self, *args, **options):
        action = options['action']
        redis_client = Redis.from_url(settings.REDIS_URL)
        
        consumer = EventConsumerWithRetry(
            redis_client=redis_client,
            service_name='dlq-manager',
            source='dlq-ops',
        )
        
        if action == 'list':
            self.cmd_list(consumer, options['limit'])
        elif action == 'inspect':
            if not options['message_id']:
                raise CommandError("--message-id required for inspect action")
            self.cmd_inspect(consumer, options['message_id'])
        elif action == 'reprocess':
            if not options['message_id']:
                raise CommandError("--message-id required for reprocess action")
            self.cmd_reprocess(consumer, options['message_id'])
        elif action == 'clear':
            self.cmd_clear(consumer, redis_client)

    def cmd_list(self, consumer, limit: int):
        """List messages in DLQ."""
        stats = consumer.get_dlq_stats()
        self.stdout.write(self.style.SUCCESS(f"\n=== DLQ Statistics ==="))
        self.stdout.write(f"Total messages in DLQ: {stats.get('dlq_message_count', 0)}")
        self.stdout.write(f"Total messages in FAILED stream: {stats.get('failed_message_count', 0)}")
        
        messages = consumer.peek_dlq(limit=limit)
        
        if not messages:
            self.stdout.write(self.style.WARNING("No messages in DLQ"))
            return
        
        self.stdout.write(self.style.SUCCESS(f"\n=== First {len(messages)} Messages in DLQ ==="))
        
        for idx, (message_id, fields) in enumerate(messages, 1):
            self.stdout.write(f"\n{idx}. Message ID: {message_id}")
            
            for key, value in fields.items():
                key_str = key.decode() if isinstance(key, bytes) else key
                val_str = value.decode() if isinstance(value, bytes) else str(value)
                
                # Abbreviate long values
                if len(val_str) > 100:
                    val_str = val_str[:97] + "..."
                
                self.stdout.write(f"   {key_str}: {val_str}")

    def cmd_inspect(self, consumer, message_id: str):
        """Inspect specific DLQ message details."""
        redis_client = consumer.redis
        
        # Get message from DLQ
        message = redis_client.hgetall(message_id)
        
        if not message:
            self.stdout.write(self.style.ERROR(f"Message {message_id} not found in DLQ"))
            return
        
        self.stdout.write(self.style.SUCCESS(f"\n=== DLQ Message Details ==="))
        self.stdout.write(f"Message ID: {message_id}")
        
        for key, value in message.items():
            key_str = key.decode() if isinstance(key, bytes) else key
            val_str = value.decode() if isinstance(value, bytes) else str(value)
            self.stdout.write(f"{key_str}: {val_str}")
        
        # Get metadata
        metadata = redis_client.hgetall(f"dlq:metadata:{message_id}")
        if metadata:
            self.stdout.write(self.style.SUCCESS("\n=== Retry Metadata ==="))
            for key, value in metadata.items():
                key_str = key.decode() if isinstance(key, bytes) else key
                val_str = value.decode() if isinstance(value, bytes) else str(value)
                self.stdout.write(f"{key_str}: {val_str}")

    def cmd_reprocess(self, consumer, message_id: str):
        """Attempt to reprocess a DLQ message."""
        self.stdout.write(f"Reprocessing message {message_id}...")
        
        try:
            success = consumer.reprocess_dlq_message(
                message_id,
                handler=self._dummy_handler
            )
            
            if success:
                self.stdout.write(self.style.SUCCESS(f"✓ Message {message_id} reprocessed successfully"))
            else:
                self.stdout.write(self.style.ERROR(f"✗ Failed to reprocess message {message_id}"))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"✗ Error reprocessing message: {str(e)}"))

    def cmd_clear(self, consumer, redis_client):
        """Clear all messages from DLQ (dangerous operation)."""
        self.stdout.write(self.style.WARNING("WARNING: This will delete all DLQ messages"))
        confirm = input("Type 'yes' to confirm: ")
        
        if confirm.lower() != 'yes':
            self.stdout.write("Cancelled")
            return
        
        try:
            dlq_count = redis_client.delete(EventConsumerWithRetry.DLQ_STREAM)
            failed_count = redis_client.delete(EventConsumerWithRetry.FAILED_STREAM)
            
            self.stdout.write(self.style.SUCCESS(f"✓ Cleared {dlq_count} DLQ and {failed_count} FAILED messages"))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error clearing DLQ: {str(e)}"))

    @staticmethod
    def _dummy_handler(source: str, event_type: str, payload: dict):
        """Dummy handler for DLQ inspection."""
        pass
