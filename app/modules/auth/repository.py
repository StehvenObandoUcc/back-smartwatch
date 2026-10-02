import uuid
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.models import AccountToken, RefreshToken


class RefreshTokenRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_hash_for_update(self, token_hash: str) -> RefreshToken | None:
        # FOR UPDATE: dos rotaciones simultáneas del mismo token no pueden ganar ambas.
        result = await self._session.execute(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash).with_for_update()
        )
        return result.scalar_one_or_none()

    async def revoke_family(self, family_id: uuid.UUID, now: datetime) -> None:
        await self._session.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )

    async def revoke_all_for_user(self, user_id: uuid.UUID, now: datetime) -> None:
        """Cierra las sesiones web del usuario (las de reloj usan `device_id`)."""
        await self._session.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )

    def add(self, token: RefreshToken) -> None:
        self._session.add(token)


class AccountTokenRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_usable_for_update(
        self, token_hash: str, purpose: str, now: datetime
    ) -> AccountToken | None:
        # FOR UPDATE: el mismo enlace usado dos veces a la vez solo gana una.
        result = await self._session.execute(
            select(AccountToken)
            .where(
                AccountToken.token_hash == token_hash,
                AccountToken.purpose == purpose,
                AccountToken.used_at.is_(None),
                AccountToken.expires_at > now,
            )
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def invalidate(self, user_id: uuid.UUID, purpose: str, now: datetime) -> None:
        await self._session.execute(
            update(AccountToken)
            .where(
                AccountToken.user_id == user_id,
                AccountToken.purpose == purpose,
                AccountToken.used_at.is_(None),
            )
            .values(used_at=now)
        )

    def add(self, token: AccountToken) -> None:
        self._session.add(token)
