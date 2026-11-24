from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_session, get_settings_dep, rate_limit
from app.core.config import Settings
from app.models import User
from app.schemas.auth import LoginRequest, RegisterRequest, TokenResponse, UserOut
from app.services.auth_service import AuthService, IssuedTokens

router = APIRouter(prefix="/auth", tags=["auth"])
_auth_limit = Depends(rate_limit("auth", "rate_limit_auth_per_minute"))


def _token_response(response: Response, issued: IssuedTokens, settings: Settings) -> TokenResponse:
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=issued.refresh_token,
        max_age=settings.refresh_token_ttl_seconds,
        path=settings.refresh_cookie_path,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
    )
    response.headers["Cache-Control"] = "no-store"
    return TokenResponse(
        access_token=issued.access_token,
        expires_in=settings.access_token_ttl_seconds,
        user=UserOut.model_validate(issued.user),
    )


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED, dependencies=[_auth_limit])
async def register(
    body: RegisterRequest,
    response: Response,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
) -> TokenResponse:
    issued = await AuthService(session, settings).register(
        email=body.email, password=body.password, display_name=body.display_name
    )
    return _token_response(response, issued, settings)


@router.post("/login", response_model=TokenResponse, dependencies=[_auth_limit])
async def login(
    body: LoginRequest,
    response: Response,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
) -> TokenResponse:
    issued = await AuthService(session, settings).login(email=body.email, password=body.password)
    return _token_response(response, issued, settings)


@router.post("/refresh", response_model=TokenResponse, dependencies=[_auth_limit])
async def refresh(
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
) -> TokenResponse:
    issued = await AuthService(session, settings).refresh(request.cookies.get(settings.refresh_cookie_name))
    return _token_response(response, issued, settings)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
) -> Response:
    await AuthService(session, settings).logout(request.cookies.get(settings.refresh_cookie_name))
    response.delete_cookie(settings.refresh_cookie_name, path=settings.refresh_cookie_path)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)) -> UserOut:
    return UserOut.model_validate(user)
