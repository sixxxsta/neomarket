from rest_framework import serializers

from .models import BlockingReason, ModerationCard


class BlockingReasonSerializer(serializers.ModelSerializer):
    class Meta:
        model = BlockingReason
        fields = ['code', 'title', 'description']


class ModerationCardSerializer(serializers.ModelSerializer):
    class Meta:
        model = ModerationCard
        fields = [
            'id',
            'product_id',
            'event_type',
            'queue_status',
            'snapshot_before',
            'snapshot_after',
            'assigned_to',
            'created_at',
            'updated_at',
        ]


class DeclineRequestSerializer(serializers.Serializer):
    reason_code = serializers.SlugField(max_length=64)
    comment = serializers.CharField(max_length=500, allow_blank=True, required=False)
    fields = serializers.ListField(child=serializers.CharField(max_length=128), required=False, allow_empty=True)


class EnqueueRequestSerializer(serializers.Serializer):
    product_id = serializers.UUIDField()
    event_type = serializers.ChoiceField(choices=ModerationCard.EventType.choices)
    snapshot_before = serializers.JSONField(required=False)
    snapshot_after = serializers.JSONField(required=False)
