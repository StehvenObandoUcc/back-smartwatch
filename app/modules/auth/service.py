from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ConflictError, UnauthorizedError
from app.core.security import (
    AccessClaims,
    AccessTokenService,
    PasswordService,
    TokenType,
    utcnow,
)
from app.modules.auth.schemas import AccessTokenOut, AuthSessionOut, LoginRequest, RegisterRequest
from app.modules.auth.tokens import RefreshTokens, invalid_refresh
from app.modules.users.models import User
from app.modules.users.repository import UserRepository
from app.modules.users.service import to_user_out

MAX_REFRESH_TOKEN_LENGTH = 512


@dataclass(frozen=True, slots=True)
class WebSession:
    """Resultado para la web: el JSON y el refresh token que irá en la cookie."""

    body: AuthSessionOut
    refresh_token: str


@dataclass(frozen=True, slots=True)
class WebRefresh:
    body: AccessTokenOut
    refresh_token: str


class AuthService:
    def __init__(
        self,
        session: AsyncSession,
        passwords: PasswordService,
        access_tokens: AccessTokenService,
        settings: Settings,
    ) -> None:
        self._session = session
        self._passwords = passwords
        self._access = access_tokens
        self._users = UserRepository(session)
        self._refresh = RefreshTokens(session)
        self._refresh_lifetime = timedelta(days=settings.user_refresh_ttl_days)

    async def register(self, data: RegisterRequest) -> WebSession:
        email = data.email.lower()
        if await self._users.email_exists(email):
            raise _email_taken()
        user = User(
            email=email,
            password_hash=await self._passwords.hash(data.password),
            display_name=data.display_name,
            role=data.role,
            timezone=data.timezone,
            locale=data.locale,
        )
        self._users.add(user)
        try:
            await self._session.flush()
        except IntegrityError:
            # Carrera: otro registro con el mismo correo entre la comprobación y el insert.
            await self._session.rollback()
            raise _email_taken() from None
        return await self._open_session(user)

    async def login(self, data: LoginRequest) -> WebSession:
        user = await self._users.get_by_email(data.email.lower())
        # Se verifica siempre (con un hash ficticio si no existe) para no revelar por tiempo
        # de respuesta qué correos están registrados.
        valid = await self._passwords.verify(user.password_hash if user else None, data.password)
        if user is None or not valid:
            raise UnauthorizedError("invalid_credentials", "Correo o contraseña incorrectos.")
        return await self._open_session(user)

    async def refresh(self, raw: str | None) -> WebRefresh:
        if not raw:
            raise UnauthorizedError("missing_refresh_token", "No hay sesión que renovar.")
        if len(raw) > MAX_REFRESH_TOKEN_LENGTH:
            raise invalid_refresh()
        now = utcnow()
        rotation = await self._refresh.rotate(
            raw, kind="user", lifetime=self._refresh_lifetime, now=now
        )
        user_id = rotation.token.user_id
        user = await self._users.get(user_id) if user_id else None
        if user is None:
            raise invalid_refresh()
        await self._session.commit()
        return WebRefresh(body=self._access_token(user), refresh_token=rotation.raw)

    async def logout(self, raw: str | None) -> None:
        if not raw or len(raw) > MAX_REFRESH_TOKEN_LENGTH:
            return
        token = await self._refresh.peek(raw)
        if token is not None and token.user_id is not None:
            await self._refresh.revoke_family(token.family_id, utcnow())
        await self._session.commit()

    async def _open_session(self, user: User) -> WebSession:
        _, raw = self._refresh.issue(user_id=user.id, lifetime=self._refresh_lifetime, now=utcnow())
        await self._session.commit()
        await self._session.refresh(user)
        return WebSession(
            body=AuthSessionOut(
                user=to_user_out(user, patient_id=None), tokens=self._access_token(user)
            ),
            refresh_token=raw,
        )

    def _access_token(self, user: User) -> AccessTokenOut:
        token = self._access.issue(
            AccessClaims(subject=user.id, token_type=TokenType.USER, role=user.role), utcnow()
        )
        return AccessTokenOut(access_token=token, expires_in=self._access.ttl_seconds)


def _email_taken() -> ConflictError:
    return ConflictError("email_taken", "Ya existe una cuenta con ese correo.")
