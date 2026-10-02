import uuid
from datetime import date

from sqlalchemy import exists, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import Cursor, before_cursor
from app.modules.medications.models import Medication, Schedule
from app.modules.patients.models import Patient
from app.modules.reports.models import Report


class ReportRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, patient_id: uuid.UUID, report_id: uuid.UUID) -> Report | None:
        result = await self._session.execute(
            select(Report).where(Report.id == report_id, Report.patient_id == patient_id)
        )
        return result.scalar_one_or_none()

    async def get_by_period(self, patient_id: uuid.UUID, period_end: date) -> Report | None:
        result = await self._session.execute(
            select(Report).where(Report.patient_id == patient_id, Report.period_end == period_end)
        )
        return result.scalar_one_or_none()

    async def list_for_patient(
        self, patient_id: uuid.UUID, cursor: Cursor | None, limit: int
    ) -> list[Report]:
        stmt = select(Report).where(Report.patient_id == patient_id)
        condition = before_cursor(Report.created_at, Report.id, cursor)
        if condition is not None:
            stmt = stmt.where(condition)
        stmt = stmt.order_by(Report.created_at.desc(), Report.id.desc()).limit(limit + 1)
        return list((await self._session.execute(stmt)).scalars())

    async def create_if_absent(
        self, patient_id: uuid.UUID, start: date, end: date, trigger: str
    ) -> bool:
        """Crea el reporte pendiente; False si ya había uno para ese periodo. Sin commit."""
        stmt = (
            insert(Report)
            .values(patient_id=patient_id, period_start=start, period_end=end, trigger=trigger)
            .on_conflict_do_nothing(index_elements=["patient_id", "period_end"])
            .returning(Report.id)
        )
        return (await self._session.execute(stmt)).first() is not None

    async def next_pending_for_update(self) -> Report | None:
        result = await self._session.execute(
            select(Report)
            .where(Report.status == "pending")
            .order_by(Report.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        return result.scalar_one_or_none()

    async def get_patient(self, patient_id: uuid.UUID) -> Patient | None:
        return await self._session.get(Patient, patient_id)

    async def patients_with_schedules(self) -> list[Patient]:
        """Pacientes con algún horario: los únicos que pueden tener dosis (y omitidas)."""
        has_schedule = exists().where(
            Schedule.medication_id == Medication.id, Medication.patient_id == Patient.id
        )
        result = await self._session.execute(select(Patient).where(has_schedule))
        return list(result.scalars())
