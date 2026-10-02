"""Verificación de correo y recuperación de contraseña.

Los tokens son de un solo uso y se guardan con hash. El correo no se envía aquí: se encola en el
outbox (misma transacción) y lo envía el worker.
"""

import uuid
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import InvalidRequestError, UnauthorizedError
from app.core.security import PasswordService, hash_token, new_opaque_token, utcnow
from app.modules.auth.models import AccountToken
from app.modules.auth.repository import AccountTokenRepository, RefreshTokenRepository
from app.modules.notifications.repository import OutboxRepository
from app.modules.users.models import User
from app.modules.users.repository import UserRepository

VERIFY = "verify_email"
RESET = "reset_password"


def _invalid_token() -> InvalidRequestError:
    return InvalidRequestError("invalid_token", "El enlace no es válido, caducó o ya se usó.")


class AccountService:
    def __init__(
        self, session: AsyncSession, passwords: PasswordService, settings: Settings
    ) -> None:
        self._session = session
        self._passwords = passwords
        self._settings = settings
        self._users = UserRepository(session)
        self._tokens = AccountTokenRepository(session)
        self._refresh = RefreshTokenRepository(session)
        self._outbox = OutboxRepository(session)

    async def _issue(self, user: User, purpose: str, ttl: timedelta, path: str) -> None:
        """Invalida los tokens anteriores, crea uno nuevo y encola el correo. Sin commit."""
        now = utcnow()
        await self._tokens.invalidate(user.id, purpose, now)
        raw = new_opaque_token()
        row = AccountToken(
            user_id=user.id,
            purpose=purpose,
            token_hash=hash_token(raw),
            expires_at=now + ttl,
            created_at=now,
        )
        self._tokens.add(row)
        await self._session.flush()
        link = f"{self._settings.web_origin}{path}?token={raw}"
        await self._outbox.enqueue(
            channel="email",
            kind=purpose,
            payload={"to": user.email, "link": link},
            dedupe_key=f"{purpose}:{row.id}",
        )

    async def send_verification(self, user: User) -> None:
        ttl = timedelta(hours=self._settings.email_verify_ttl_hours)
        await self._issue(user, VERIFY, ttl, "/verify-email")

    async def resend_verification(self, user_id: uuid.UUID) -> bool:
        """True si se encoló un correo; False si el correo ya estaba verificado."""
        user = await self._users.get(user_id)
        if user is None:
            raise UnauthorizedError("invalid_token", "La sesión ya no es válida.")
        if user.email_verified_at is not None:
            return False
        await self.send_verification(user)
        await self._session.commit()
        return True

    async def verify_email(self, token: str) -> None:
        now = utcnow()
        row = await self._tokens.get_usable_for_update(hash_token(token), VERIFY, now)
        user = await self._users.get(row.user_id) if row else None
        if row is None or user is None:
            raise _invalid_token()
        row.used_at = now
        if user.email_verified_at is None:
            user.email_verified_at = now
        await self._session.commit()

    async def forgot_password(self, email: str) -> None:
        """No revela si la cuenta existe: sin cuenta o sin correo verificado no pasa nada."""
        user = await self._users.get_by_email(email.lower())
        if user is None or user.email_verified_at is None:
            return
        ttl = timedelta(minutes=self._settings.password_reset_ttl_minutes)
        await self._issue(user, RESET, ttl, "/reset-password")
        await self._session.commit()

    async def reset_password(self, token: str, new_password: str) -> None:
        now: datetime = utcnow()
        row = await self._tokens.get_usable_for_update(hash_token(token), RESET, now)
        user = await self._users.get(row.user_id) if row else None
        if row is None or user is None:
            raise _invalid_token()
        user.password_hash = await self._passwords.hash(new_password)
        row.used_at = now
        # Quien tenía la contraseña vieja (o una sesión robada) pierde el acceso.
        await self._refresh.revoke_all_for_user(user.id, now)
        await self._session.commit()
