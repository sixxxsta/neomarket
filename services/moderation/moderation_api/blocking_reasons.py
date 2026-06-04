from .models import BlockingReason


def list_active_blocking_reasons(hard_block=None):
    queryset = BlockingReason.objects.filter(is_active=True).order_by('title')
    if hard_block is not None:
        queryset = queryset.filter(hard_only=hard_block)
    return queryset
