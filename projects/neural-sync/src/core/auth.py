"""JWT authentication utilities per ADR-002."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
import bcrypt

from src.core.settings import settings

security = HTTPBearer(auto_error=False)

# bcrypt operates on the first 72 bytes of the password (older passlib truncated
# silently; we replicate that to keep behaviour stable across the 72-byte limit).
_BCRYPT_MAX_BYTES = 72


def hash_password(password: str) -> str:
    pw = password.encode("utf-8")[:_BCRYPT_MAX_BYTES]
    return bcrypt.hashpw(pw, bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    pw = plain.encode("utf-8")[:_BCRYPT_MAX_BYTES]
    try:
        return bcrypt.checkpw(pw, hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def create_access_token(user_id: str, role: str, developer_profile_id: Optional[str] = None) -> str:
    now = datetime.now(timezone.utc)
    payload: dict = {
        "sub": user_id,
        "role": role,
        "type": "access",
        "iat": now,
        "exp": now + timedelta(seconds=settings.jwt_access_token_ttl_seconds),
    }
    if developer_profile_id is not None:
        payload["dev_profile_id"] = developer_profile_id
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_refresh_token(user_id: str, role: str) -> str:
    """Signed, stateless refresh token (JWT). Stored client-side in an HttpOnly
    cookie; validated on /auth/refresh. `type` claim distinguishes it from an
    access token so an access token cannot be replayed as a refresh token."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "role": role,
        "type": "refresh",
        "iat": now,
        "exp": now + timedelta(seconds=settings.jwt_refresh_token_ttl_seconds),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_refresh_token(token: str) -> dict:
    """Validate a refresh JWT. Raises 401 on bad signature, expiry, or if the
    token is not of type 'refresh'."""
    try:
        payload = jwt.decode(
            token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
        )
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        ) from exc
    if payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Provided token is not a refresh token",
        )
    return payload


def decode_access_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        ) from exc
    if payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Provided token is not an access token",
        )
    return payload


class TokenPayload:
    def __init__(self, sub: str, role: str, developer_profile_id: Optional[str] = None):
        self.user_id = sub
        self.role = role
        self.developer_profile_id = developer_profile_id


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> TokenPayload:
    if not credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing authentication token")
    payload = decode_access_token(credentials.credentials)
    return TokenPayload(
        sub=payload["sub"],
        role=payload["role"],
        developer_profile_id=payload.get("dev_profile_id"),
    )


async def require_manager(current_user: TokenPayload = Depends(get_current_user)) -> TokenPayload:
    if current_user.role != "manager":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Manager role required")
    return current_user


async def require_admin(current_user: TokenPayload = Depends(get_current_user)) -> TokenPayload:
    """Gate for the Admin View / system-override endpoints (Task04 §6)."""
    if current_user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin role required")
    return current_user


async def require_admin_or_manager(
    current_user: TokenPayload = Depends(get_current_user),
) -> TokenPayload:
    """Gate for operations both managers and admins may perform (e.g. allocation overrides)."""
    if current_user.role not in ("admin", "manager"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Manager or admin role required"
        )
    return current_user


async def get_optional_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> Optional[TokenPayload]:
    """Return None if no credentials provided (for endpoints that support optional auth)."""
    if not credentials:
        return None
    try:
        payload = decode_access_token(credentials.credentials)
        return TokenPayload(
            sub=payload["sub"],
            role=payload["role"],
            developer_profile_id=payload.get("dev_profile_id"),
        )
    except HTTPException:
        return None
