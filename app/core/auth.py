"""Dependencias de autenticación: `current_user` (web) y `current_device` (reloj)."""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.deps import AccessTokensDep
from app.core.errors import ForbiddenError, UnauthorizedError
from app.core.security import AccessClaims, InvalidTokenError, TokenType

_bearer = HTTPBearer(auto_error=False)

BearerDep = Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]


@dataclass(frozen=True, slots=True)
class CurrentUser:
    id: uuid.UUID
    role: str


@dataclass(frozen=True, slots=True)
class CurrentDevice:
    id: uuid.UUID
    patient_id: uuid.UUID


# Comprobación de que el reloj sigue vinculado; la registra el módulo devices en create_app.
DeviceActiveCheck = Callable[[Request, uuid.UUID], Awaitable[bool]]


def _claims(
    credentials: HTTPAuthorizationCredentials | None, tokens: AccessTokensDep
) -> AccessClaims:
    if credentials is None:
        raise UnauthorizedError("missing_token", "Falta el token de acceso.")
    try:
        return tokens.decode(credentials.credentials)
    except InvalidTokenError:
        raise UnauthorizedError(
            "invalid_token", "El token de acceso no es válido o caducó."
        ) from None


async def current_user(credentials: BearerDep, tokens: AccessTokensDep) -> CurrentUser:
    claims = _claims(credentials, tokens)
    if claims.token_type is not TokenType.USER or claims.role is None:
        raise ForbiddenError("wrong_token_type", "Esta ruta requiere una sesión de usuario.")
    return CurrentUser(id=claims.subject, role=claims.role)


async def current_device(
    request: Request, credentials: BearerDep, tokens: AccessTokensDep
) -> CurrentDevice:
    claims = _claims(credentials, tokens)
    if claims.token_type is not TokenType.DEVICE or claims.patient_id is None:
        raise ForbiddenError("wrong_token_type", "Esta ruta requiere el token de un reloj.")
    check: DeviceActiveCheck | None = getattr(request.app.state, "device_active_check", None)
    if check is not None and not await check(request, claims.subject):
        raise UnauthorizedError("device_unpaired", "El reloj ya no está vinculado.")
    return CurrentDevice(id=claims.subject, patient_id=claims.patient_id)


CurrentUserDep = Annotated[CurrentUser, Depends(current_user)]
CurrentDeviceDep = Annotated[CurrentDevice, Depends(current_device)]
