"""Reportes de adherencia para la web: crear (asíncrono), consultar y descargar el PDF.

La generación la hace el worker (`processor.py`): aquí solo se crea la fila `pending`.
"""

import uuid
from datetime import date, timedelta
from typing import Literal, cast
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser
from app.core.errors import ConflictError, NotFoundError, UnprocessableError
from app.core.pagination import Page, next_cursor
from app.core.security import utcnow
from app.modules.patients.service import PatientService
from app.modules.plan.generator import local_today
from app.modules.reports.models import Report
from app.modules.reports.repository import ReportRepository
from app.modules.reports.schemas import ReportCreate, ReportOut, ReportPage, ReportSummaryOut

PERIOD_DAYS = 7


def report_not_found() -> NotFoundError:
    return NotFoundError("report_not_found", "El reporte no existe.")


def to_report_out(report: Report) -> ReportOut:
    ready = report.status == "ready" and report.summary is not None
    return ReportOut(
        id=report.id,
        patient_id=report.patient_id,
        period_start=report.period_start,
        period_end=report.period_end,
        trigger=cast(Literal["weekly", "manual"], report.trigger),
        status=cast(Literal["pending", "ready", "failed"], report.status),
        created_at=report.created_at,
        generated_at=report.generated_at,
        summary=ReportSummaryOut.model_validate(report.summary) if ready else None,
    )


class ReportService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._patients = PatientService(session)
        self._reports = ReportRepository(session)

    async def check_access(self, user: CurrentUser, patient_id: uuid.UUID) -> None:
        await self._patients.health_access(user, patient_id)

    async def list(self, user: CurrentUser, patient_id: uuid.UUID, page: Page) -> ReportPage:
        await self._patients.health_access(user, patient_id)
        rows = await self._reports.list_for_patient(patient_id, page.cursor, page.limit)
        items, cursor = next_cursor(rows, page.limit, lambda r: (r.created_at, r.id))
        return ReportPage(items=[to_report_out(r) for r in items], next_cursor=cursor)

    async def create(
        self, user: CurrentUser, patient_id: uuid.UUID, data: ReportCreate | None
    ) -> ReportOut:
        patient = await self._patients.health_access(user, patient_id)
        today = local_today(ZoneInfo(patient.timezone), utcnow())
        end = (data.period_end if data else None) or today - timedelta(days=1)
        if end > today:
            raise UnprocessableError(["body", "periodEnd"], "periodEnd no puede ser futuro")
        start = end - timedelta(days=PERIOD_DAYS - 1)
        await self._reports.create_if_absent(patient_id, start, end, "manual")
        report = await self._reports.get_by_period(patient_id, end)
        if report is None:  # no debería pasar: se acaba de crear o ya existía
            raise report_not_found()
        if report.status == "failed":
            report.status = "pending"  # pedirlo otra vez reintenta un reporte fallido
        await self._session.commit()
        return to_report_out(report)

    async def _get(self, user: CurrentUser, patient_id: uuid.UUID, report_id: uuid.UUID) -> Report:
        await self._patients.health_access(user, patient_id)
        report = await self._reports.get(patient_id, report_id)
        if report is None:
            raise report_not_found()
        return report

    async def get(
        self, user: CurrentUser, patient_id: uuid.UUID, report_id: uuid.UUID
    ) -> ReportOut:
        return to_report_out(await self._get(user, patient_id, report_id))

    async def pdf(
        self, user: CurrentUser, patient_id: uuid.UUID, report_id: uuid.UUID
    ) -> tuple[bytes, date]:
        report = await self._get(user, patient_id, report_id)
        if report.status != "ready" or report.pdf is None:
            raise ConflictError("report_not_ready", "El reporte aún no está listo.")
        return report.pdf, report.period_end
