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


def normalize_field_reports(field_reports, error_cls=None):
    if error_cls is None:
        from .soft_block import SoftBlockError

        error_cls = SoftBlockError

    normalized = []
    for item in field_reports or []:
        if not isinstance(item, dict):
            continue
        field_name = str(item.get('field_name') or item.get('field') or '').strip()
        message = str(item.get('message') or '').strip()
        if not field_name:
            continue
        if field_name not in ALLOWED_FIELD_NAMES:
            raise error_cls(
                'INVALID_FIELD_NAME',
                f'Unknown field name: {field_name}',
                400,
            )
        normalized.append(
            {
                'field': field_name,
                'message': message or 'Требуется исправление после модерации',
            }
        )
    return normalized
