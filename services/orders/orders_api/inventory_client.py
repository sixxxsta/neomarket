import requests
from django.conf import settings


def inventory_call(url, payload):
    try:
        response = requests.post(
            url,
            json=payload,
            headers={"X-Service-Key": settings.INTERNAL_SERVICE_KEY},
            timeout=settings.B2B_TIMEOUT,
        )
    except requests.RequestException:
        return None, "unavailable"
    try:
        data = response.json()
    except ValueError:
        data = {}
    return (response.status_code, data), None
