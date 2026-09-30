"""Contraseñas (Argon2id), tokens de acceso (JWT) y refresh tokens opacos."""

import asyncio
import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.core.config import Settings

_JWT_ALGORITHM = "HS256"


class TokenType(StrEnum):
    USER = "user"
    DEVICE = "device"


class PasswordService:
    """Argon2id en un hilo aparte: el hash es CPU intensivo y no debe bloquear el event loop."""

    def __init__(self, settings: Settings) -> None:
        self._hasher = PasswordHasher(
            time_cost=settings.argon2_time_cost,
            memory_cost=settings.argon2_memory_cost_kib,
            parallelism=settings.argon2_parallelism,
        )
        # Hash de referencia para igualar el tiempo de respuesta cuando el usuario no existe.
        self._dummy_hash = self._hasher.hash(secrets.token_urlsafe(16))

    async def hash(self, password: str) -> str:
        return await asyncio.to_thread(self._hasher.hash, password)

    async def verify(self, password_hash: str | None, password: str) -> bool:
        return await asyncio.to_thread(self._verify, password_hash or self._dummy_hash, password)

    def _verify(self, password_hash: str, password: str) -> bool:
        try:
            return self._hasher.verify(password_hash, password)
        except (VerificationError, InvalidHashError):
            return False


@dataclass(frozen=True, slots=True)
class AccessClaims:
    subject: uuid.UUID
    token_type: TokenType
    # Rol del usuario o paciente vinculado del reloj.
    role: str | None = None
    patient_id: uuid.UUID | None = None


class InvalidTokenError(Exception):
    pass


class AccessTokenService:
    def __init__(self, settings: Settings) -> None:
        self._secret = settings.jwt_secret.get_secret_value()
        self._issuer = settings.jwt_issuer
        self.ttl_seconds = settings.access_token_ttl_seconds

    def issue(self, claims: AccessClaims, now: datetime) -> str:
        payload: dict[str, object] = {
            "iss": self._issuer,
            "sub": str(claims.subject),
            "typ": claims.token_type.value,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(seconds=self.ttl_seconds)).timestamp()),
            "jti": uuid.uuid4().hex,
        }
        if claims.role is not None:
            payload["role"] = claims.role
        if claims.patient_id is not None:
            payload["pid"] = str(claims.patient_id)
        return jwt.encode(payload, self._secret, algorithm=_JWT_ALGORITHM)

    def decode(self, token: str) -> AccessClaims:
        try:
            payload = jwt.decode(
                token,
                self._secret,
                algorithms=[_JWT_ALGORITHM],
                issuer=self._issuer,
                options={"require": ["exp", "iat", "sub", "typ", "iss"]},
            )
            pid = payload.get("pid")
            return AccessClaims(
                subject=uuid.UUID(payload["sub"]),
                token_type=TokenType(payload["typ"]),
                role=payload.get("role"),
                patient_id=uuid.UUID(pid) if pid else None,
            )
        except (jwt.PyJWTError, ValueError, KeyError) as exc:
            raise InvalidTokenError from exc


def new_opaque_token() -> str:
    """Secreto aleatorio para refresh tokens y device codes (256 bits)."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Solo se guarda el SHA-256 de los secretos; son aleatorios, no necesitan sal ni Argon2."""
    return hashlib.sha256(token.encode()).hexdigest()


def utcnow() -> datetime:
    return datetime.now(UTC)
