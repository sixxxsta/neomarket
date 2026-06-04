from rest_framework import serializers

ALLOWED_FIELD_NAMES = frozenset(
    {
        'title',
        'description',
        'images',
        'price',
        'category',
        'skus',
        'characteristics',
        'brand',
    }
)


class FieldReportItemSerializer(serializers.Serializer):
    field_name = serializers.CharField(max_length=64)
    message = serializers.CharField(max_length=500, allow_blank=True, required=False, default='')
