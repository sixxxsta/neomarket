from .hard_block import HardBlockError, hard_block_product
from .models import BlockingReason
from .soft_block import SoftBlockError, soft_block_product
from .terminal import assert_product_not_hard_blocked


class DeclineError(Exception):
    def __init__(self, code, message, http_status):
        self.code = code
        self.message = message
        self.http_status = http_status
        super().__init__(message)


def decline_product(product_id, moderator, blocking_reason_id, comment='', field_reports=None):
    assert_product_not_hard_blocked(product_id, DeclineError)

    reason = BlockingReason.objects.filter(code=blocking_reason_id, is_active=True).first()
    if not reason:
        raise DeclineError(
            'BLOCKING_REASON_NOT_FOUND',
            'Blocking reason does not exist',
            400,
        )

    if reason.hard_only:
        return hard_block_product(
            product_id,
            moderator,
            blocking_reason_id,
            comment=comment,
            field_reports=field_reports,
        )

    return soft_block_product(
        product_id,
        moderator,
        blocking_reason_id,
        comment=comment,
        field_reports=field_reports,
    )
