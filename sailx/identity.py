"""Short-lived service tokens carrying the researcher's identity to the SQL MCP server.

The agent never holds database credentials. It mints (or forwards) a token naming the
user and project; the SQL MCP server verifies it and checks project membership itself.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

import jwt

from .config import IdentitySettings


@dataclass(frozen=True)
class Caller:
    user: str
    project: str
    channel: str = "web"


class TokenError(PermissionError):
    pass


MIN_SECRET_LENGTH = 32


def _check_secret(secret: str | None) -> str:
    if not secret:
        raise TokenError("SERVICE_JWT_SECRET is not set; cannot mint or verify service tokens")
    if len(secret.encode()) < MIN_SECRET_LENGTH:
        raise TokenError(f"SERVICE_JWT_SECRET must be at least {MIN_SECRET_LENGTH} bytes "
                         "(e.g. generate with: openssl rand -hex 32)")
    return secret


def mint_token(caller: Caller, settings: IdentitySettings) -> str:
    _check_secret(settings.shared_secret)
    now = int(time.time())
    claims = {
        "sub": caller.user.lower(),
        "project": caller.project,
        "channel": caller.channel,
        "iss": settings.issuer,
        "aud": settings.audience,
        "iat": now,
        "exp": now + settings.ttl_seconds,
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(claims, settings.shared_secret, algorithm="HS256")


_jwks_client: jwt.PyJWKClient | None = None


def verify_token(token: str, settings: IdentitySettings) -> Caller:
    global _jwks_client
    options = {"require": ["sub", "project", "exp", "iat"]}
    try:
        if settings.jwks_url:
            _jwks_client = _jwks_client or jwt.PyJWKClient(settings.jwks_url)
            key = _jwks_client.get_signing_key_from_jwt(token).key
            claims = jwt.decode(token, key, algorithms=["RS256", "ES256"], audience=settings.audience,
                                issuer=settings.issuer, options=options)
        elif settings.shared_secret:
            claims = jwt.decode(token, _check_secret(settings.shared_secret), algorithms=["HS256"], audience=settings.audience,
                                issuer=settings.issuer, options=options)
        else:
            raise TokenError("No SERVICE_JWT_SECRET or SERVICE_JWKS_URL configured")
    except jwt.PyJWTError as exc:
        raise TokenError(f"Invalid service token: {exc}") from exc
    return Caller(user=claims["sub"], project=claims["project"], channel=claims.get("channel", "web"))
