import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Response, status

from app.core.auth import CurrentUserDep
from app.core.deps import ClientIpDep, RateLimiterDep, SessionDep, SettingsDep
from app.core.errors import NotFoundError
from app.core.pagination import PageDep
from app.core.params import uuid_path
from app.modules.consents.schemas import ConsentListOut, ConsentOut, ConsentUpdate, Purpose
from app.modules.patients.schemas import (
    CaregiverInvitationOut,
    CareLinkOut,
    CareLinkPage,
    PatientCreate,
    PatientOut,
    PatientPage,
    PatientUpdate,
)
from app.modules.patients.service import PatientService, patient_not_found

router = APIRouter(tags=["patients"])


def _link_not_found() -> NotFoundError:
    return NotFoundError("care_link_not_found", "Ese usuario no es cuidador del paciente.")


PatientIdPath = Annotated[uuid.UUID, uuid_path("patientId", patient_not_found)]
CaregiverIdPath = Annotated[uuid.UUID, uuid_path("userId", _link_not_found)]


def get_patient_service(session: SessionDep) -> PatientService:
    return PatientService(session)


PatientServiceDep = Annotated[PatientService, Depends(get_patient_service)]


@router.get("/patients", operation_id="listPatients", summary="Pacientes accesibles")
async def list_patients(
    user: CurrentUserDep, page: PageDep, service: PatientServiceDep
) -> PatientPage:
    return await service.list(user, page)


@router.post(
    "/patients",
    operation_id="createPatient",
    summary="Crear paciente gestionado",
    status_code=status.HTTP_201_CREATED,
)
async def create_patient(
    data: PatientCreate, user: CurrentUserDep, service: PatientServiceDep
) -> PatientOut:
    return await service.create_managed(user, data)


@router.get("/patients/{patientId}", operation_id="getPatient", summary="Detalle de un paciente")
async def get_patient(
    patient_id: PatientIdPath,
    user: CurrentUserDep,
    service: PatientServiceDep,
) -> PatientOut:
    return await service.get(user, patient_id)


@router.patch(
    "/patients/{patientId}", operation_id="updatePatient", summary="Actualizar un paciente"
)
async def update_patient(
    patient_id: PatientIdPath,
    data: PatientUpdate,
    user: CurrentUserDep,
    service: PatientServiceDep,
) -> PatientOut:
    return await service.update(user, patient_id, data)


@router.get(
    "/patients/{patientId}/consents",
    operation_id="listPatientConsents",
    summary="Consentimientos de un paciente",
)
async def list_patient_consents(
    patient_id: PatientIdPath,
    user: CurrentUserDep,
    service: PatientServiceDep,
) -> ConsentListOut:
    return await service.list_consents(user, patient_id)


@router.put(
    "/patients/{patientId}/consents/{purpose}",
    operation_id="setPatientConsent",
    summary="Otorgar o retirar un consentimiento del paciente",
)
async def set_patient_consent(
    patient_id: PatientIdPath,
    purpose: Purpose,
    data: ConsentUpdate,
    user: CurrentUserDep,
    service: PatientServiceDep,
) -> ConsentOut:
    return await service.set_consent(user, patient_id, purpose, data)


@router.get(
    "/patients/{patientId}/caregivers",
    operation_id="listPatientCaregivers",
    summary="Cuidadores de un paciente",
)
async def list_patient_caregivers(
    patient_id: PatientIdPath,
    page: PageDep,
    user: CurrentUserDep,
    service: PatientServiceDep,
) -> CareLinkPage:
    return await service.list_caregivers(user, patient_id, page)


@router.delete(
    "/patients/{patientId}/caregivers/{userId}",
    operation_id="revokePatientCaregiver",
    summary="Revocar el vínculo de un cuidador",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_patient_caregiver(
    patient_id: PatientIdPath,
    caregiver_id: CaregiverIdPath,
    user: CurrentUserDep,
    service: PatientServiceDep,
) -> Response:
    await service.revoke_caregiver(user, patient_id, caregiver_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/patients/{patientId}/caregiver-invitations",
    operation_id="createCaregiverInvitation",
    summary="Invitar a un cuidador",
    status_code=status.HTTP_201_CREATED,
)
async def create_caregiver_invitation(
    patient_id: PatientIdPath,
    user: CurrentUserDep,
    service: PatientServiceDep,
    limiter: RateLimiterDep,
    settings: SettingsDep,
) -> CaregiverInvitationOut:
    # Se valida el acceso antes de contar, para no revelar pacientes ajenos con un 429.
    await service.access(user, patient_id)
    await limiter.hit("invitation:patient", str(patient_id), settings.rate_invitation_patient)
    return await service.create_invitation(user, patient_id)


@router.post(
    "/caregiver-invitations/{code}/accept",
    operation_id="acceptCaregiverInvitation",
    summary="Aceptar una invitación",
    status_code=status.HTTP_201_CREATED,
)
async def accept_caregiver_invitation(
    code: Annotated[str, Path()],
    user: CurrentUserDep,
    service: PatientServiceDep,
    limiter: RateLimiterDep,
    ip: ClientIpDep,
    settings: SettingsDep,
) -> CareLinkOut:
    await limiter.hit("invitation:accept:user", str(user.id), settings.rate_invitation_accept_user)
    await limiter.hit("invitation:accept:ip", ip, settings.rate_invitation_accept_ip)
    return await service.accept_invitation(user, code)
