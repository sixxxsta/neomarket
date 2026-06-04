from dataclasses import dataclass
from uuid import UUID

import jwt
from django.conf import settings


@dataclass
class AuthContext:
    actor: str
    user_id: UUID | None
    roles: set[str]


def _parse_uuid(value):
    if not value:
        return None
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


def _normalize_roles(payload):
    raw_roles = payload.get("roles") or payload.get("role") or payload.get("scope") or []
    if isinstance(raw_roles, str):
        raw_roles = raw_roles.replace(",", " ").split()
    normalized = set()
    for role in raw_roles:
        if role:
            normalized.add(str(role).strip().upper())
    if payload.get("is_admin"):
        normalized.add("ADMIN")
    return normalized


def _decode_token(token: str) -> dict:
    algorithm = settings.JWT_ALGORITHM
    decode_kwargs = {
        "algorithms": [algorithm],
        "options": {
            "verify_signature": True,
            "verify_exp": True,
            "verify_aud": bool(settings.JWT_AUDIENCE),
            "verify_iss": bool(settings.JWT_ISSUER),
        },
    }

    if settings.JWT_AUDIENCE:
        decode_kwargs["audience"] = settings.JWT_AUDIENCE
    if settings.JWT_ISSUER:
        decode_kwargs["issuer"] = settings.JWT_ISSUER

    if algorithm.startswith("HS"):
        key = settings.JWT_SECRET
    else:
        key = settings.JWT_PUBLIC_KEY

    if not key:
        raise jwt.InvalidKeyError("JWT verification key is not configured")

    return jwt.decode(token, key=key, **decode_kwargs)


def authenticate_request(request):
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header.split(" ", 1)[1].strip()
        payload = _decode_token(token)
        user_id = _parse_uuid(payload.get("sub") or payload.get("user_id"))
        actor = str(payload.get("preferred_username") or payload.get("sub") or payload.get("user_id") or "jwt-user")
        return AuthContext(actor=actor, user_id=user_id, roles=_normalize_roles(payload))

    if settings.DEBUG:
        actor = request.headers.get("X-Moderator-Id") or request.headers.get("X-User-Id")
        if actor:
            roles = set(
                role.strip().upper()
                for role in request.headers.get("X-Roles", "MODERATOR").split(",")
                if role.strip()
            )
            return AuthContext(actor=actor, user_id=_parse_uuid(actor), roles=roles)

    raise jwt.InvalidTokenError("Missing bearer token")


def has_any_role(context: AuthContext, required_roles: set[str]) -> bool:
    return bool(context.roles.intersection(required_roles))
