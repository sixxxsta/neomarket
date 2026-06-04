from .models import ModerationCard


def product_is_hard_blocked(product_id):
    return ModerationCard.objects.filter(
        product_id=product_id,
        queue_status=ModerationCard.QueueStatus.HARD_BLOCKED,
    ).exists()


def assert_product_not_hard_blocked(product_id, error_cls, code='HARD_BLOCKED_TERMINAL', message='Product is hard blocked'):
    if product_is_hard_blocked(product_id):
        raise error_cls(code, message, 403)
