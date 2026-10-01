import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from app.core.auth import CurrentUserDep
from app.core.deps import SessionDep
from app.core.pagination import PageDep
from app.core.params import uuid_path
from app.modules.medications.schemas import (
    MedicationCreate,
    MedicationOut,
    MedicationPage,
    MedicationUpdate,
    ScheduleCreate,
    ScheduleList,
    ScheduleOut,
    ScheduleUpdate,
)
from app.modules.medications.service import (
    MedicationService,
    medication_not_found,
    schedule_not_found,
)
from app.modules.patients.service import patient_not_found

router = APIRouter(tags=["medications"])

PatientIdPath = Annotated[uuid.UUID, uuid_path("patientId", patient_not_found)]
MedicationIdPath = Annotated[uuid.UUID, uuid_path("medicationId", medication_not_found)]
ScheduleIdPath = Annotated[uuid.UUID, uuid_path("scheduleId", schedule_not_found)]

_BASE = "/patients/{patientId}/medications"


def get_medication_service(session: SessionDep) -> MedicationService:
    return MedicationService(session)


ServiceDep = Annotated[MedicationService, Depends(get_medication_service)]


@router.get(_BASE, operation_id="listMedications", summary="Medicamentos de un paciente")
async def list_medications(
    patient_id: PatientIdPath,
    page: PageDep,
    user: CurrentUserDep,
    service: ServiceDep,
    include_archived: Annotated[bool, Query(alias="includeArchived")] = False,
) -> MedicationPage:
    return await service.list(user, patient_id, include_archived, page)


@router.post(
    _BASE,
    operation_id="createMedication",
    summary="Crear un medicamento",
    status_code=status.HTTP_201_CREATED,
)
async def create_medication(
    patient_id: PatientIdPath, data: MedicationCreate, user: CurrentUserDep, service: ServiceDep
) -> MedicationOut:
    return await service.create(user, patient_id, data)


@router.get(_BASE + "/{medicationId}", operation_id="getMedication", summary="Un medicamento")
async def get_medication(
    patient_id: PatientIdPath,
    medication_id: MedicationIdPath,
    user: CurrentUserDep,
    service: ServiceDep,
) -> MedicationOut:
    return await service.get(user, patient_id, medication_id)


@router.patch(
    _BASE + "/{medicationId}", operation_id="updateMedication", summary="Editar un medicamento"
)
async def update_medication(
    patient_id: PatientIdPath,
    medication_id: MedicationIdPath,
    data: MedicationUpdate,
    user: CurrentUserDep,
    service: ServiceDep,
) -> MedicationOut:
    return await service.update(user, patient_id, medication_id, data)


@router.delete(
    _BASE + "/{medicationId}",
    operation_id="archiveMedication",
    summary="Archivar un medicamento",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def archive_medication(
    patient_id: PatientIdPath,
    medication_id: MedicationIdPath,
    user: CurrentUserDep,
    service: ServiceDep,
) -> Response:
    await service.archive(user, patient_id, medication_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    _BASE + "/{medicationId}/schedules",
    operation_id="listSchedules",
    summary="Horarios de un medicamento",
)
async def list_schedules(
    patient_id: PatientIdPath,
    medication_id: MedicationIdPath,
    user: CurrentUserDep,
    service: ServiceDep,
) -> ScheduleList:
    return await service.list_schedules(user, patient_id, medication_id)


@router.post(
    _BASE + "/{medicationId}/schedules",
    operation_id="createSchedule",
    summary="Añadir un horario",
    status_code=status.HTTP_201_CREATED,
)
async def create_schedule(
    patient_id: PatientIdPath,
    medication_id: MedicationIdPath,
    data: ScheduleCreate,
    user: CurrentUserDep,
    service: ServiceDep,
) -> ScheduleOut:
    return await service.create_schedule(user, patient_id, medication_id, data)


@router.patch(
    _BASE + "/{medicationId}/schedules/{scheduleId}",
    operation_id="updateSchedule",
    summary="Editar un horario",
)
async def update_schedule(
    patient_id: PatientIdPath,
    medication_id: MedicationIdPath,
    schedule_id: ScheduleIdPath,
    data: ScheduleUpdate,
    user: CurrentUserDep,
    service: ServiceDep,
) -> ScheduleOut:
    return await service.update_schedule(user, patient_id, medication_id, schedule_id, data)


@router.delete(
    _BASE + "/{medicationId}/schedules/{scheduleId}",
    operation_id="deleteSchedule",
    summary="Eliminar un horario",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_schedule(
    patient_id: PatientIdPath,
    medication_id: MedicationIdPath,
    schedule_id: ScheduleIdPath,
    user: CurrentUserDep,
    service: ServiceDep,
) -> Response:
    await service.delete_schedule(user, patient_id, medication_id, schedule_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
