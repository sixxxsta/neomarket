from rest_framework import serializers

from .field_reports import FieldReportItemSerializer
from .models import BlockingReason, ModerationCard


class BlockingReasonSerializer(serializers.ModelSerializer):
    blocking_reason_id = serializers.SlugField(source='code', read_only=True)
    hard_block = serializers.BooleanField(source='hard_only', read_only=True)

    class Meta:
        model = BlockingReason
        fields = ['blocking_reason_id', 'title', 'description', 'hard_block']


class ModerationCardSerializer(serializers.ModelSerializer):
    class Meta:
        model = ModerationCard
        fields = [
            'id',
            'product_id',
            'event_type',
            'queue_status',
            'priority_queue',
            'snapshot_before',
            'snapshot_after',
            'assigned_to',
            'created_at',
            'updated_at',
        ]


class TicketResponseSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    product_id = serializers.UUIDField()
    seller_id = serializers.UUIDField(allow_null=True)
    kind = serializers.CharField()
    status = serializers.CharField()
    queue_priority = serializers.IntegerField()
    created_at = serializers.DateTimeField()


class BlockDecisionRequestSerializer(serializers.Serializer):
    blocking_reason_ids = serializers.ListField(
        child=serializers.UUIDField(),
        min_length=1,
        required=True,
    )
    comment = serializers.CharField(max_length=500, allow_blank=True, required=False, default='')
    field_reports = FieldReportItemSerializer(many=True, required=False, allow_empty=True)
    fields = serializers.ListField(child=serializers.CharField(max_length=128), required=False, allow_empty=True)


DeclineRequestSerializer = BlockDecisionRequestSerializer


class GetNextRequestSerializer(serializers.Serializer):
    queue_id = serializers.IntegerField(required=False, allow_null=True, min_value=1, max_value=4)


class EnqueueRequestSerializer(serializers.Serializer):
    product_id = serializers.UUIDField()
    event_type = serializers.ChoiceField(choices=ModerationCard.EventType.choices)
    snapshot_before = serializers.JSONField(required=False)
    snapshot_after = serializers.JSONField(required=False)
