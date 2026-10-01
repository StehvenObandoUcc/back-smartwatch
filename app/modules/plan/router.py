import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Response, status

from app.core.auth import CurrentDeviceDep, CurrentUserDep
from app.core.deps import SessionDep
from app.core.params import uuid_path
from app.modules.patients.service import patient_not_found
from app.modules.plan.schemas import PlanOut
from app.modules.plan.service import PlanService, etag_matches

router = APIRouter(tags=["plan"])

PatientIdPath = Annotated[uuid.UUID, uuid_path("patientId", patient_not_found)]


def get_plan_service(session: SessionDep) -> PlanService:
    return PlanService(session)


PlanServiceDep = Annotated[PlanService, Depends(get_plan_service)]


@router.get(
    "/devices/me/plan",
    operation_id="getMyPlan",
    summary="Plan de 7 días del reloj",
    response_model=PlanOut,
    responses={304: {"description": "El plan no cambió."}},
)
async def get_my_plan(
    device: CurrentDeviceDep,
    service: PlanServiceDep,
    if_none_match: Annotated[str | None, Header()] = None,
) -> Response:
    plan, etag = await service.for_device(device)
    if etag_matches(if_none_match, etag):
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers={"ETag": etag})
    return Response(
        content=plan.model_dump_json(by_alias=True),
        media_type="application/json",
        headers={"ETag": etag},
    )


@router.get(
    "/patients/{patientId}/plan",
    operation_id="getPatientPlan",
    summary="Plan de 7 días de un paciente (agenda web)",
)
async def get_patient_plan(
    patient_id: PatientIdPath, user: CurrentUserDep, service: PlanServiceDep
) -> PlanOut:
    return await service.for_user(user, patient_id)
