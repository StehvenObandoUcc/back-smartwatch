"""Emisión y rotación de refresh tokens, compartida por la sesión web y la del reloj."""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import UnauthorizedError
from app.core.security import hash_token, new_opaque_token
from app.modules.auth.models import RefreshToken
from app.modules.auth.repository import RefreshTokenRepository


@dataclass(frozen=True, slots=True)
class Rotation:
    token: RefreshToken  # el nuevo registro (misma familia)
    raw: str  # el nuevo refresh token en claro, solo para devolverlo al cliente


def invalid_refresh() -> UnauthorizedError:
    return UnauthorizedError("invalid_refresh_token", "La sesión no es válida o caducó.")


class RefreshTokens:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = RefreshTokenRepository(session)

    def issue(
        self,
        *,
        lifetime: timedelta,
        now: datetime,
        user_id: uuid.UUID | None = None,
        device_id: uuid.UUID | None = None,
    ) -> tuple[RefreshToken, str]:
        """Abre una familia nueva para un usuario o para un reloj."""
        return self._new(
            family_id=uuid.uuid4(), lifetime=lifetime, now=now, user_id=user_id, device_id=device_id
        )

    async def rotate(
        self, raw: str, *, kind: Literal["user", "device"], lifetime: timedelta, now: datetime
    ) -> Rotation:
        """Consume `raw` y emite su sucesor. Detecta reutilización y revoca la familia.

        Un token del otro tipo (de reloj en la sesión web o al revés) se trata como inválido.
        No hace commit: el llamador confirma junto con el resto de cambios. Si hay reutilización,
        la revocación se confirma aquí mismo antes de lanzar el error.
        """
        current = await self._repo.get_by_hash_for_update(hash_token(raw))
        if (
            current is None
            or _kind(current) != kind
            or current.revoked_at is not None
            or current.expires_at <= now
        ):
            raise invalid_refresh()
        if current.rotated_at is not None:
            await self._repo.revoke_family(current.family_id, now)
            await self._session.commit()
            raise UnauthorizedError(
                "refresh_token_reused",
                "Se reutilizó un token ya usado; la sesión se ha cerrado por seguridad.",
            )
        current.rotated_at = now
        token, new_raw = self._new(
            family_id=current.family_id,
            lifetime=lifetime,
            now=now,
            user_id=current.user_id,
            device_id=current.device_id,
        )
        return Rotation(token=token, raw=new_raw)

    async def peek(self, raw: str) -> RefreshToken | None:
        return await self._repo.get_by_hash_for_update(hash_token(raw))

    async def revoke_family(self, family_id: uuid.UUID, now: datetime) -> None:
        await self._repo.revoke_family(family_id, now)

    def _new(
        self,
        *,
        family_id: uuid.UUID,
        lifetime: timedelta,
        now: datetime,
        user_id: uuid.UUID | None,
        device_id: uuid.UUID | None,
    ) -> tuple[RefreshToken, str]:
        raw = new_opaque_token()
        token = RefreshToken(
            family_id=family_id,
            token_hash=hash_token(raw),
            user_id=user_id,
            device_id=device_id,
            expires_at=now + lifetime,
            created_at=now,
        )
        self._repo.add(token)
        return token, raw


def _kind(token: RefreshToken) -> str:
    return "device" if token.device_id is not None else "user"
