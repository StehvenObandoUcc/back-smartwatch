import uuid
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Response, status

from app.core.auth import CurrentUserDep
from app.core.deps import RateLimiterDep, SessionDep, SettingsDep
from app.core.pagination import PageDep
from app.core.params import uuid_path
from app.modules.patients.service import patient_not_found
from app.modules.reports.schemas import ReportCreate, ReportOut, ReportPage
from app.modules.reports.service import ReportService, report_not_found

router = APIRouter(tags=["reports"])

PatientIdPath = Annotated[uuid.UUID, uuid_path("patientId", patient_not_found)]
ReportIdPath = Annotated[uuid.UUID, uuid_path("reportId", report_not_found)]

_BASE = "/patients/{patientId}/reports"


def get_report_service(session: SessionDep) -> ReportService:
    return ReportService(session)


ServiceDep = Annotated[ReportService, Depends(get_report_service)]


@router.get(_BASE, operation_id="listReports", summary="Reportes semanales de un paciente")
async def list_reports(
    patient_id: PatientIdPath, page: PageDep, user: CurrentUserDep, service: ServiceDep
) -> ReportPage:
    return await service.list(user, patient_id, page)


@router.post(
    _BASE,
    operation_id="createReport",
    summary="Pedir un reporte a mano",
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_report(
    patient_id: PatientIdPath,
    user: CurrentUserDep,
    service: ServiceDep,
    limiter: RateLimiterDep,
    settings: SettingsDep,
    data: Annotated[ReportCreate | None, Body()] = None,
) -> ReportOut:
    # Primero el acceso (404 sin vínculo) y luego el límite: un 429 no revela pacientes ajenos.
    await service.check_access(user, patient_id)
    await limiter.hit("report:patient", str(patient_id), settings.rate_report_patient)
    return await service.create(user, patient_id, data)


@router.get(_BASE + "/{reportId}", operation_id="getReport", summary="Un reporte")
async def get_report(
    patient_id: PatientIdPath, report_id: ReportIdPath, user: CurrentUserDep, service: ServiceDep
) -> ReportOut:
    return await service.get(user, patient_id, report_id)


@router.get(
    _BASE + "/{reportId}/pdf",
    operation_id="downloadReportPdf",
    summary="Descargar el reporte en PDF",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}, "description": "Documento PDF."}},
)
async def download_report_pdf(
    patient_id: PatientIdPath, report_id: ReportIdPath, user: CurrentUserDep, service: ServiceDep
) -> Response:
    content, period_end = await service.pdf(user, patient_id, report_id)
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="reporte-{period_end}.pdf"'},
    )
