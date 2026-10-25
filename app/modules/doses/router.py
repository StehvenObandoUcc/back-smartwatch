import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response, status

from app.core.auth import CurrentDeviceDep, CurrentUserDep
from app.core.deps import SessionDep
from app.core.pagination import PageDep
from app.core.params import uuid_path
from app.modules.doses.schemas import (
    Adherence,
    DeviceAdherence,
    DoseEventBatch,
    DoseEventBatchResult,
    DoseHistoryPage,
)
from app.modules.doses.service import DoseService
from app.modules.patients.service import patient_not_found

router = APIRouter(tags=["doses"])

PatientIdPath = Annotated[uuid.UUID, uuid_path("patientId", patient_not_found)]
FromQuery = Annotated[date | None, Query(alias="from")]
ToQuery = Annotated[date | None, Query()]


def get_dose_service(session: SessionDep) -> DoseService:
    return DoseService(session)


DoseServiceDep = Annotated[DoseService, Depends(get_dose_service)]


@router.post(
    "/devices/me/dose-events",
    operation_id="createMyDoseEvents",
    summary="Subir eventos de toma (lote, idempotente)",
)
async def create_my_dose_events(
    data: DoseEventBatch, device: CurrentDeviceDep, service: DoseServiceDep
) -> DoseEventBatchResult:
    return await service.record(device, data)


@router.post(
    "/patients/{patientId}/dose-events",
    operation_id="createPatientDoseEvents",
    summary="Registrar tomas desde la web (lote, idempotente)",
)
async def create_patient_dose_events(
    patient_id: PatientIdPath,
    data: DoseEventBatch,
    user: CurrentUserDep,
    service: DoseServiceDep,
) -> DoseEventBatchResult:
    return await service.record_for_patient(user, patient_id, data)


def _event_id(value: Annotated[str, Path(alias="eventId")]) -> uuid.UUID | None:
    # Mal formado = no existe: deshacer es idempotente y responde 204 igual.
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


EventIdPath = Annotated[uuid.UUID | None, Depends(_event_id)]


@router.delete(
    "/devices/me/dose-events/{eventId}",
    operation_id="deleteMyDoseEvent",
    summary="Deshacer una toma desde el reloj",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_my_dose_event(
    event_id: EventIdPath, device: CurrentDeviceDep, service: DoseServiceDep
) -> Response:
    await service.undo(device, event_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/patients/{patientId}/dose-events/{eventId}",
    operation_id="deletePatientDoseEvent",
    summary="Deshacer una toma desde la web",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_patient_dose_event(
    patient_id: PatientIdPath,
    event_id: EventIdPath,
    user: CurrentUserDep,
    service: DoseServiceDep,
) -> Response:
    await service.undo_for_patient(user, patient_id, event_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/devices/me/adherence",
    operation_id="getMyDeviceAdherence",
    summary="Adherencia del paciente del reloj",
)
async def get_my_device_adherence(
    device: CurrentDeviceDep,
    service: DoseServiceDep,
    from_: FromQuery = None,
    to: ToQuery = None,
) -> DeviceAdherence:
    return await service.device_adherence(device, from_, to)


@router.get(
    "/patients/{patientId}/dose-history",
    operation_id="listDoseHistory",
    summary="Historial de dosis de un paciente",
)
async def list_dose_history(
    patient_id: PatientIdPath,
    page: PageDep,
    user: CurrentUserDep,
    service: DoseServiceDep,
    from_: FromQuery = None,
    to: ToQuery = None,
) -> DoseHistoryPage:
    return await service.history(user, patient_id, from_, to, page)


@router.get(
    "/patients/{patientId}/adherence",
    operation_id="getAdherence",
    summary="Porcentaje de adherencia de un paciente",
)
async def get_adherence(
    patient_id: PatientIdPath,
    user: CurrentUserDep,
    service: DoseServiceDep,
    from_: FromQuery = None,
    to: ToQuery = None,
) -> Adherence:
    return await service.adherence(user, patient_id, from_, to)
