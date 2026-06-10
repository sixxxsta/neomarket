from uuid import UUID

from .hard_block import HardBlockError, hard_block_ticket
from .models import BlockingReason
from .soft_block import SoftBlockError, soft_block_ticket
from .terminal import assert_product_not_hard_blocked


class BlockError(Exception):
    def __init__(self, code, message, http_status):
        self.code = code
        self.message = message
        self.http_status = http_status
        super().__init__(message)


DeclineError = BlockError


def _resolve_blocking_reasons(blocking_reason_ids):
    if not blocking_reason_ids:
        raise BlockError(
            'BLOCKING_REASON_REQUIRED',
            'blocking_reason_ids must contain at least one item',
            400,
        )

    parsed_ids = []
    for raw_id in blocking_reason_ids:
        try:
            parsed_ids.append(UUID(str(raw_id)))
        except (TypeError, ValueError) as exc:
            raise BlockError(
                'INVALID_BLOCKING_REASON_ID',
                'blocking_reason_ids must contain valid UUID values',
                400,
            ) from exc

    reasons = list(
        BlockingReason.objects.filter(reason_uuid__in=parsed_ids, is_active=True)
    )
    if len(reasons) != len(set(parsed_ids)):
        raise BlockError(
            'BLOCKING_REASON_NOT_FOUND',
            'Blocking reason does not exist',
            400,
        )

    reasons_by_id = {reason.reason_uuid: reason for reason in reasons}
    return [reasons_by_id[item] for item in parsed_ids]


def block_ticket(ticket_id, moderator, blocking_reason_ids, comment='', field_reports=None):
    from .models import ModerationCard

    card = ModerationCard.objects.filter(id=ticket_id).first()
    if not card:
        raise BlockError('NOT_FOUND', 'Ticket is not in moderation review', 404)

    assert_product_not_hard_blocked(card.product_id, BlockError)

    reasons = _resolve_blocking_reasons(blocking_reason_ids)
    primary_reason = reasons[0]

    if primary_reason.hard_only:
        return hard_block_ticket(
            ticket_id,
            moderator,
            primary_reason,
            comment=comment,
            field_reports=field_reports,
        )

    return soft_block_ticket(
        ticket_id,
        moderator,
        primary_reason,
        comment=comment,
        field_reports=field_reports,
    )
