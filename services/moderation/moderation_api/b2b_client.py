import requests
from django.conf import settings


def post_moderation_decision(payload):
    url = settings.B2B_MODERATION_EVENTS_URL
    if not url:
        return None, 'unconfigured'

    try:
        response = requests.post(
            url,
            json=payload,
            headers={'X-Service-Key': settings.INTERNAL_SERVICE_KEY},
            timeout=settings.B2B_REQUEST_TIMEOUT,
        )
    except requests.RequestException:
        return None, 'unavailable'

    try:
        data = response.json()
    except ValueError:
        data = {}
    return (response.status_code, data), None
