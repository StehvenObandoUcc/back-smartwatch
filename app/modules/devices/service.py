"""Vinculación del reloj (flujo tipo device code, RFC 8628) y gestión de relojes."""

import re
import secrets
import uuid
from datetime import timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentDevice, CurrentUser
from app.core.config import Settings
from app.core.errors import ConflictError, InvalidRequestError, NotFoundError, UnauthorizedError
from app.core.pagination import Page, next_cursor
from app.core.security import (
    AccessClaims,
    AccessTokenService,
    TokenType,
    hash_token,
    new_opaque_token,
    utcnow,
)
from app.modules.auth.schemas import TokenPairOut
from app.modules.auth.tokens import RefreshTokens, invalid_refresh
from app.modules.devices.models import Device, PairingCode
from app.modules.devices.repository import DeviceRepository, PairingRepository
from app.modules.devices.schemas import (
    USER_CODE_ALPHABET,
    DeviceCodeGrant,
    DeviceOut,
    DevicePage,
    DeviceRefreshGrant,
    PairingCodeOut,
    PairingCodeRequest,
)
from app.modules.patients.service import PatientService

USER_CODE_PATTERN = re.compile(r"[BCDFGHJKLMNPQRSTVWXZ]{4}-[BCDFGHJKLMNPQRSTVWXZ]{4}")
SLOW_DOWN_STEP_SECONDS = 5  # RFC 8628 §3.5


def to_device_out(device: Device) -> DeviceOut:
    return DeviceOut(
        id=device.id,
        patient_id=device.patient_id,
        model=device.model,
        paired_at=device.paired_at,
        last_seen_at=device.last_seen_at,
        has_push_token=device.push_token is not None,
    )


def device_not_found() -> NotFoundError:
    return NotFoundError("device_not_found", "El reloj no existe o no tienes acceso.")


def pairing_code_not_found() -> NotFoundError:
    return NotFoundError(
        "pairing_code_not_found", "El código no existe, caducó o ya se usó. Pide uno nuevo."
    )


def _new_user_code() -> str:
    letters = "".join(secrets.choice(USER_CODE_ALPHABET) for _ in range(8))
    return f"{letters[:4]}-{letters[4:]}"


class DeviceService:
    def __init__(
        self, session: AsyncSession, access_tokens: AccessTokenService, settings: Settings
    ) -> None:
        self._session = session
        self._access = access_tokens
        self._settings = settings
        self._devices = DeviceRepository(session)
        self._pairings = PairingRepository(session)
        self._refresh = RefreshTokens(session)
        self._patients = PatientService(session)
        self._refresh_lifetime = timedelta(days=settings.device_refresh_idle_days)

    # ─── Paso 1: el reloj pide un código ──────────────────────────────────────

    async def create_pairing_code(self, data: PairingCodeRequest) -> PairingCodeOut:
        now = utcnow()
        device_code = new_opaque_token()
        for _ in range(5):
            user_code = _new_user_code()
            pairing = PairingCode(
                user_code_hash=hash_token(user_code),
                device_code_hash=hash_token(device_code),
                model=data.model,
                app_version=data.app_version,
                expires_at=now + timedelta(seconds=self._settings.pairing_code_ttl_seconds),
                interval_seconds=self._settings.pairing_poll_interval_seconds,
                created_at=now,
            )
            self._devices.add(pairing)
            try:
                await self._session.commit()
            except IntegrityError:
                # Colisión (improbable) con un código existente: se genera otro.
                await self._session.rollback()
                continue
            return PairingCodeOut(
                code=user_code,
                device_code=device_code,
                expires_in=self._settings.pairing_code_ttl_seconds,
                interval=pairing.interval_seconds,
            )
        raise ConflictError("pairing_code_collision", "No se pudo generar un código. Reinténtalo.")

    # ─── Paso 2: la web confirma ──────────────────────────────────────────────

    async def confirm(self, user: CurrentUser, code: str, patient_id: uuid.UUID) -> DeviceOut:
        code = code.upper()
        if not USER_CODE_PATTERN.fullmatch(code):
            raise pairing_code_not_found()
        now = utcnow()
        pairing = await self._pairings.get_pending_by_user_code(hash_token(code), now)
        if pairing is None:
            raise pairing_code_not_found()
        await self._patients.access(user, patient_id)  # 404 patient_not_found si no hay vínculo

        # Un solo reloj activo por paciente: el anterior se desvincula y pierde sus tokens.
        for previous in await self._devices.active_for_patient_for_update(patient_id):
            previous.revoked_at = now
            await self._devices.revoke_tokens(previous.id, now)
        await self._session.flush()

        device = Device(
            patient_id=patient_id,
            model=pairing.model,
            app_version=pairing.app_version,
            paired_at=now,
            created_at=now,
        )
        self._devices.add(device)
        await self._session.flush()
        pairing.confirmed_at = now
        pairing.confirmed_by_user_id = user.id
        pairing.device_id = device.id
        await self._session.commit()
        return to_device_out(device)

    # ─── Paso 3: el reloj obtiene o renueva sus tokens ────────────────────────

    async def token(self, grant: DeviceCodeGrant | DeviceRefreshGrant) -> TokenPairOut:
        if isinstance(grant, DeviceCodeGrant):
            return await self._exchange_device_code(grant.device_code)
        return await self._refresh_device(grant.refresh_token)

    async def _exchange_device_code(self, device_code: str) -> TokenPairOut:
        now = utcnow()
        pairing = await self._pairings.get_by_device_code(hash_token(device_code))
        if pairing is None or pairing.consumed_at is not None or pairing.expires_at <= now:
            raise _device_code_error("expired_token", "El código caducó o ya se usó; pide otro.")

        too_fast = pairing.last_polled_at is not None and now - pairing.last_polled_at < timedelta(
            seconds=pairing.interval_seconds
        )
        pairing.last_polled_at = now
        if too_fast:
            pairing.interval_seconds += SLOW_DOWN_STEP_SECONDS
            await self._session.commit()
            raise _device_code_error(
                "slow_down",
                f"Consultas demasiado seguidas: espera {pairing.interval_seconds} s.",
            )
        if pairing.confirmed_at is None or pairing.device_id is None:
            await self._session.commit()
            raise _device_code_error(
                "authorization_pending", "El código aún no se ha confirmado desde la web."
            )

        device = await self._devices.get_active(pairing.device_id)
        if device is None:  # desvinculado antes de recoger los tokens
            pairing.consumed_at = now
            await self._session.commit()
            raise _device_code_error("expired_token", "El código caducó o ya se usó; pide otro.")

        pairing.consumed_at = now
        device.last_seen_at = now
        _, refresh = self._refresh.issue(
            device_id=device.id, lifetime=self._refresh_lifetime, now=now
        )
        await self._session.commit()
        return self._token_pair(device, refresh)

    async def _refresh_device(self, raw: str) -> TokenPairOut:
        now = utcnow()
        rotation = await self._refresh.rotate(
            raw, kind="device", lifetime=self._refresh_lifetime, now=now
        )
        device_id = rotation.token.device_id
        device = await self._devices.get_active(device_id) if device_id else None
        if device is None:
            raise invalid_refresh()
        device.last_seen_at = now
        await self._session.commit()
        return self._token_pair(device, rotation.raw)

    def _token_pair(self, device: Device, refresh: str) -> TokenPairOut:
        access = self._access.issue(
            AccessClaims(
                subject=device.id, token_type=TokenType.DEVICE, patient_id=device.patient_id
            ),
            utcnow(),
        )
        return TokenPairOut(
            access_token=access, refresh_token=refresh, expires_in=self._access.ttl_seconds
        )

    # ─── El reloj autenticado ─────────────────────────────────────────────────

    async def _current(self, current: CurrentDevice) -> Device:
        device = await self._devices.get_active(current.id)
        if device is None:
            raise UnauthorizedError("device_unpaired", "El reloj ya no está vinculado.")
        return device

    async def get_me(self, current: CurrentDevice) -> DeviceOut:
        return to_device_out(await self._current(current))

    async def set_push_token(self, current: CurrentDevice, token: str) -> None:
        device = await self._current(current)
        device.push_token = token
        await self._session.commit()

    # ─── Gestión desde la web ─────────────────────────────────────────────────

    async def list_for_patient(
        self, user: CurrentUser, patient_id: uuid.UUID, page: Page
    ) -> DevicePage:
        await self._patients.access(user, patient_id)
        rows = await self._devices.list_active(patient_id, page.cursor, page.limit)
        items, cursor = next_cursor(rows, page.limit, lambda d: (d.created_at, d.id))
        return DevicePage(items=[to_device_out(d) for d in items], next_cursor=cursor)

    async def unpair(self, user: CurrentUser, device_id: uuid.UUID) -> None:
        device = await self._devices.get(device_id)
        if device is None:
            raise device_not_found()
        try:
            await self._patients.access(user, device.patient_id)
        except NotFoundError:
            raise device_not_found() from None
        if device.revoked_at is not None:
            return  # idempotente
        now = utcnow()
        device.revoked_at = now
        await self._devices.revoke_tokens(device.id, now)
        await self._session.commit()

    async def is_active(self, device_id: uuid.UUID) -> bool:
        return await self._devices.is_active(device_id)


def _device_code_error(code: str, detail: str) -> InvalidRequestError:
    titles = {
        "authorization_pending": "Vinculación pendiente",
        "slow_down": "Consulta más despacio",
        "expired_token": "Código caducado",
    }
    return InvalidRequestError(code, detail, title=titles[code])
