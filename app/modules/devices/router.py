import uuid
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Path, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.auth import CurrentDeviceDep, CurrentUserDep
from app.core.deps import (
    AccessTokensDep,
    ClientIpDep,
    RateLimiterDep,
    SessionDep,
    SettingsDep,
)
from app.core.pagination import PageDep
from app.core.params import uuid_path
from app.modules.auth.schemas import TokenPairOut
from app.modules.devices.repository import DeviceRepository
from app.modules.devices.schemas import (
    DeviceOut,
    DevicePage,
    DeviceTokenRequest,
    PairingCodeOut,
    PairingCodeRequest,
    PairingConfirm,
    PushTokenUpdate,
)
from app.modules.devices.service import DeviceService, device_not_found
from app.modules.patients.service import patient_not_found

router = APIRouter(tags=["devices"])

PatientIdPath = Annotated[uuid.UUID, uuid_path("patientId", patient_not_found)]
DeviceIdPath = Annotated[uuid.UUID, uuid_path("deviceId", device_not_found)]


def get_device_service(
    session: SessionDep, access_tokens: AccessTokensDep, settings: SettingsDep
) -> DeviceService:
    return DeviceService(session, access_tokens, settings)


DeviceServiceDep = Annotated[DeviceService, Depends(get_device_service)]


async def device_active_check(request: Request, device_id: uuid.UUID) -> bool:
    """Registrada en `app.state`: `current_device` rechaza relojes desvinculados."""
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with factory() as session:
        return await DeviceRepository(session).is_active(device_id)


@router.post(
    "/devices/pairing-codes",
    operation_id="createPairingCode",
    summary="El reloj pide un código de vinculación",
    status_code=status.HTTP_201_CREATED,
)
async def create_pairing_code(
    data: PairingCodeRequest,
    service: DeviceServiceDep,
    limiter: RateLimiterDep,
    ip: ClientIpDep,
    settings: SettingsDep,
) -> PairingCodeOut:
    await limiter.hit("pairing:create:ip", ip, settings.rate_pairing_create_ip)
    return await service.create_pairing_code(data)


@router.post(
    "/devices/pairing-codes/{code}/confirm",
    operation_id="confirmPairingCode",
    summary="Confirmar la vinculación desde la web",
)
async def confirm_pairing_code(
    code: Annotated[str, Path()],
    data: PairingConfirm,
    user: CurrentUserDep,
    service: DeviceServiceDep,
    limiter: RateLimiterDep,
    ip: ClientIpDep,
    settings: SettingsDep,
) -> DeviceOut:
    await limiter.hit("pairing:confirm:user", str(user.id), settings.rate_pairing_confirm_user)
    await limiter.hit("pairing:confirm:ip", ip, settings.rate_pairing_confirm_ip)
    return await service.confirm(user, code, data.patient_id)


@router.post(
    "/devices/token",
    operation_id="requestDeviceToken",
    summary="El reloj obtiene o renueva sus tokens",
)
async def request_device_token(
    grant: Annotated[DeviceTokenRequest, Body()],
    service: DeviceServiceDep,
    limiter: RateLimiterDep,
    ip: ClientIpDep,
    settings: SettingsDep,
) -> TokenPairOut:
    await limiter.hit("device:token:ip", ip, settings.rate_device_token_ip)
    return await service.token(grant)


@router.get("/devices/me", operation_id="getMyDevice", summary="El reloj consulta su vinculación")
async def get_my_device(device: CurrentDeviceDep, service: DeviceServiceDep) -> DeviceOut:
    return await service.get_me(device)


@router.put(
    "/devices/me/push-token",
    operation_id="setMyPushToken",
    summary="Registrar el token FCM del reloj",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def set_my_push_token(
    data: PushTokenUpdate, device: CurrentDeviceDep, service: DeviceServiceDep
) -> Response:
    await service.set_push_token(device, data.token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/patients/{patientId}/devices",
    operation_id="listPatientDevices",
    summary="Relojes de un paciente",
)
async def list_patient_devices(
    patient_id: PatientIdPath, page: PageDep, user: CurrentUserDep, service: DeviceServiceDep
) -> DevicePage:
    return await service.list_for_patient(user, patient_id, page)


@router.delete(
    "/devices/{deviceId}",
    operation_id="unpairDevice",
    summary="Desvincular un reloj",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def unpair_device(
    device_id: DeviceIdPath, user: CurrentUserDep, service: DeviceServiceDep
) -> Response:
    await service.unpair(user, device_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
