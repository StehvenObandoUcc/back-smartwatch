import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.auth import CurrentDeviceDep, CurrentUserDep
from app.core.deps import SessionDep
from app.core.pagination import PageDep
from app.core.params import uuid_path
from app.modules.doses.schemas import (
    Adherence,
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
