from __future__ import annotations

import secrets
from typing import Optional

from fastapi import Header, Request

from app.config import Settings
from app.errors import AuthenticationError
from app.service import ChatService


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_service(request: Request) -> ChatService:
    return request.app.state.service


async def require_api_key(
    request: Request,
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
) -> None:
    settings: Settings = request.app.state.settings
    if not settings.auth_enabled:
        return
    expected = f"Bearer {settings.api_key}"
    if not authorization or not secrets.compare_digest(authorization, expected):
        raise AuthenticationError()
