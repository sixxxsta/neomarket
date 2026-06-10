def deliver_moderation_decision(payload, *, error_cls):
    from .b2b_client import post_moderation_decision

    result, error_kind = post_moderation_decision(payload)
    if error_kind == 'unconfigured':
        raise error_cls('B2B_NOT_CONFIGURED', 'B2B moderation endpoint is not configured', 503)
    if error_kind == 'unavailable':
        raise error_cls('B2B_UNAVAILABLE', 'B2B service is temporarily unavailable', 503)
    http_status, _data = result
    if http_status == 404:
        raise error_cls('PRODUCT_NOT_FOUND', 'Product not found in B2B catalog', 404)
    if http_status >= 300:
        raise error_cls('B2B_UNAVAILABLE', 'B2B service rejected moderation decision', 503)
