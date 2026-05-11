from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from api_agent.settings import Settings, get_settings


@dataclass(frozen=True)
class AuthContext:
    user_id: str


async def require_auth(
    settings: Annotated[Settings, Depends(get_settings)],
    x_agent_token: Annotated[str | None, Header(alias="X-Agent-Token")] = None,
    x_user_id: Annotated[str | None, Header(alias="X-User-Id")] = None,
) -> AuthContext:
    if not settings.auth_token:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="AGENT_AUTH_TOKEN is not configured",
        )
    if x_agent_token != settings.auth_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-Agent-Token",
        )
    if not x_user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-User-Id",
        )
    return AuthContext(user_id=x_user_id)
