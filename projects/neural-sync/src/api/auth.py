"""Authentication router — login (issues access token + HttpOnly refresh
cookie) and refresh (validates + rotates the refresh cookie). Stateless JWT
refresh per ADR-002; no server-side token store in Phase 1."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from src.core.auth import (
    create_access_token,
    create_refresh_token,
    decode_refresh_token,
    verify_password,
)
from src.core.settings import settings
from src.db.models import UserAccount
from src.db.session import get_db

router = APIRouter(prefix="/auth", tags=["auth"])

_REFRESH_COOKIE = "refresh_token"
_COOKIE_PATH = "/api/v1/auth"  # cookie is only ever sent to auth endpoints


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = 3600
    user_id: uuid.UUID
    role: str


def _set_refresh_cookie(response: Response, user_id: str, role: str) -> None:
    response.set_cookie(
        key=_REFRESH_COOKIE,
        value=create_refresh_token(user_id, role),
        max_age=settings.jwt_refresh_token_ttl_seconds,
        httponly=True,
        samesite=settings.cookie_samesite,
        secure=settings.cookie_secure,
        path=_COOKIE_PATH,
    )


@router.post("/login", response_model=LoginResponse)
async def login(
    payload: LoginRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> LoginResponse:
    result = await db.execute(
        select(UserAccount).where(UserAccount.username == payload.username)
    )
    user = result.scalar_one_or_none()
    if not user or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    if not user.is_active:
        raise HTTPException(status_code=401, detail="Account is inactive")

    _set_refresh_cookie(response, str(user.id), user.role)
    return LoginResponse(
        access_token=create_access_token(str(user.id), user.role),
        token_type="bearer",
        expires_in=settings.jwt_access_token_ttl_seconds,
        user_id=user.id,
        role=user.role,
    )


@router.post("/refresh", response_model=LoginResponse)
async def refresh(
    response: Response,
    refresh_token: Optional[str] = Cookie(default=None),
) -> LoginResponse:
    if not refresh_token:
        raise HTTPException(status_code=401, detail="Missing refresh token cookie")
    payload = decode_refresh_token(refresh_token)
    user_id, role = payload["sub"], payload["role"]

    _set_refresh_cookie(response, user_id, role)  # rotate
    return LoginResponse(
        access_token=create_access_token(user_id, role),
        token_type="bearer",
        expires_in=settings.jwt_access_token_ttl_seconds,
        user_id=uuid.UUID(user_id),
        role=role,
    )
