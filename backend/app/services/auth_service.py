"""Authentication: registration, login, refresh-token rotation with reuse detection, logout."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import (
    create_access_token,
    hash_password,
    hash_refresh_token,
    new_refresh_token,
    verify_password,
)
from app.core.config import Settings
from app.core.errors import AuthError, ConflictError, ForbiddenError
from app.core.logging import get_logger
from app.core.time import utcnow
from app.models import User
from app.repositories.users import RefreshTokenRepository, UserRepository

log = get_logger(__name__)

# Used to keep login timing similar whether or not the account exists.
_DUMMY_HASH = hash_password("dummy-password-for-timing")


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class IssuedTokens:
    user: User
    access_token: str
    refresh_token: str


class AuthService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.users = UserRepository(session)
        self.tokens = RefreshTokenRepository(session)

    async def register(self, *, email: str, password: str, display_name: str) -> IssuedTokens:
        first = (await self.session.execute(select(User.id).limit(1))).first() is None
        if not first and not self.settings.registration_open:
            raise ForbiddenError(
                "Sign-ups are closed on this installation. Ask an administrator for an account.",
                code="registration_closed",
            )
        if await self.users.get_by_email(email):
            raise ConflictError("An account with this email already exists", code="email_taken")
        try:
            user = await self.users.create(
                email=email, password_hash=hash_password(password), display_name=display_name
            )
            # The first account administers the installation; ADMIN_EMAILS can name more.
            user.is_admin = first or user.email.lower() in self.settings.admin_email_list
            issued = await self._issue(user, family_id=uuid.uuid4())
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise ConflictError("An account with this email already exists", code="email_taken") from exc
        log.info("auth.registered", user_id=str(user.id))
        return issued

    async def login(self, *, email: str, password: str) -> IssuedTokens:
        user = await self.users.get_by_email(email)
        if user is None:
            verify_password(password, _DUMMY_HASH)
            raise AuthError("Invalid email or password", code="invalid_credentials")
        if not verify_password(password, user.password_hash) or not user.is_active:
            raise AuthError("Invalid email or password", code="invalid_credentials")
        issued = await self._issue(user, family_id=uuid.uuid4())
        await self.session.commit()
        log.info("auth.login", user_id=str(user.id))
        return issued

    async def refresh(self, refresh_token: str | None) -> IssuedTokens:
        if not refresh_token:
            raise AuthError("Missing refresh token", code="invalid_refresh")
        record = await self.tokens.get_by_hash(hash_refresh_token(refresh_token))
        if record is None:
            raise AuthError("Invalid refresh token", code="invalid_refresh")
        if record.revoked_at is not None or record.rotated_at is not None:
            # Reuse of a rotated/revoked token: assume theft, kill the whole session family.
            await self.tokens.revoke_family(record.family_id)
            await self.session.commit()
            log.warning("auth.refresh_reuse_detected", user_id=str(record.user_id))
            raise AuthError("Refresh token is no longer valid", code="invalid_refresh")
        if _aware(record.expires_at) <= utcnow():
            raise AuthError("Refresh token expired", code="invalid_refresh")
        user = await self.users.get(record.user_id)
        if user is None or not user.is_active:
            raise AuthError("Invalid refresh token", code="invalid_refresh")

        record.rotated_at = utcnow()
        issued = await self._issue(user, family_id=record.family_id)
        await self.session.commit()
        return issued

    async def logout(self, refresh_token: str | None) -> None:
        if not refresh_token:
            return
        record = await self.tokens.get_by_hash(hash_refresh_token(refresh_token))
        if record is not None:
            await self.tokens.revoke_family(record.family_id)
            await self.session.commit()

    async def _issue(self, user: User, *, family_id: uuid.UUID) -> IssuedTokens:
        plain = new_refresh_token()
        await self.tokens.create(
            user_id=user.id,
            family_id=family_id,
            token_hash=hash_refresh_token(plain),
            expires_at=utcnow() + timedelta(seconds=self.settings.refresh_token_ttl_seconds),
        )
        return IssuedTokens(user=user, access_token=create_access_token(user.id, self.settings), refresh_token=plain)


async def ensure_admins(session: AsyncSession, settings: Settings) -> None:
    """Startup: promote ADMIN_EMAILS; if accounts exist but none is an admin, the oldest active one becomes
    admin (so an installation can never lock itself out of its Configuration page)."""
    emails = settings.admin_email_list
    if emails:
        rows = await session.execute(select(User).where(func.lower(User.email).in_(emails), User.is_admin.is_(False)))
        for user in rows.scalars():
            user.is_admin = True
            log.info("auth.admin_promoted", user_id=str(user.id), reason="ADMIN_EMAILS")
    has_admin = (await session.execute(select(User.id).where(User.is_admin.is_(True)).limit(1))).first()
    if has_admin is None:
        oldest = (
            await session.execute(select(User).where(User.is_active.is_(True)).order_by(User.created_at).limit(1))
        ).scalar_one_or_none()
        if oldest is not None:
            oldest.is_admin = True
            log.info("auth.admin_promoted", user_id=str(oldest.id), reason="no_admin")
    await session.commit()
