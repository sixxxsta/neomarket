from rest_framework import serializers

CANONICAL_FIELD_NAMES = frozenset(
    {
        'title',
        'description',
        'product_images',
        'sku_name',
        'sku_image',
        'sku_price',
        'category',
        'characteristics',
        'brand',
    }
)

FIELD_ALIASES = {
    'images': 'product_images',
    'image': 'product_images',
    'price': 'sku_price',
    'skus': 'sku_name',
}


class FieldReportItemSerializer(serializers.Serializer):
    field_path = serializers.CharField(max_length=64)
    message = serializers.CharField(max_length=500, allow_blank=True, required=False, default='')
    sku_id = serializers.UUIDField(required=False, allow_null=True)


def _canonical_field_name(raw_name):
    field_name = str(raw_name or '').strip()
    if not field_name:
        return ''
    return FIELD_ALIASES.get(field_name, field_name)


def normalize_field_reports(field_reports, error_cls=None):
    if error_cls is None:
        from .soft_block import SoftBlockError

        error_cls = SoftBlockError

    normalized = []
    for item in field_reports or []:
        if not isinstance(item, dict):
            continue
        field_name = _canonical_field_name(
            item.get('field_path') or item.get('field_name') or item.get('field')
        )
        comment = str(item.get('message') or item.get('comment') or '').strip()
        sku_id = item.get('sku_id')
        if not field_name:
            continue
        if field_name not in CANONICAL_FIELD_NAMES:
            raise error_cls(
                'INVALID_FIELD_NAME',
                f'Unknown field name: {field_name}',
                400,
            )
        report = {
            'field_name': field_name,
            'comment': comment or 'Требуется исправление после модерации',
        }
        if sku_id:
            report['sku_id'] = str(sku_id)
        normalized.append(report)
    return normalized
