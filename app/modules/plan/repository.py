import uuid
from collections.abc import Collection
from datetime import date

from sqlalchemy import Select, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.medications.models import Medication, Schedule
from app.modules.patients.models import Patient
from app.modules.plan.generator import ScheduleRule, parse_times


def _rules_stmt(patient_id: uuid.UUID) -> Select[Schedule, Medication]:
    return (
        select(Schedule, Medication)
        .join(Medication, Medication.id == Schedule.medication_id)
        .where(Medication.patient_id == patient_id)
    )


def _to_rule(schedule: Schedule, medication: Medication) -> ScheduleRule:
    return ScheduleRule(
        schedule_id=schedule.id,
        medication_id=medication.id,
        medication_name=medication.name,
        dosage=medication.dosage,
        instructions=medication.instructions,
        color=medication.color,
        times=parse_times(schedule.times),
        days_of_week=frozenset(schedule.days_of_week),
        start_date=schedule.start_date,
        end_date=schedule.end_date,
        effective_from=schedule.effective_from,
        medication_archived_at=medication.archived_at,
    )


class PlanRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_patient(self, patient_id: uuid.UUID) -> Patient | None:
        return await self._session.get(Patient, patient_id)

    async def rules(
        self, patient_id: uuid.UUID, first_day: date, last_day: date, *, include_archived: bool
    ) -> list[ScheduleRule]:
        """Horarios del paciente que pueden generar dosis entre las dos fechas locales."""
        stmt = _rules_stmt(patient_id).where(
            Schedule.start_date <= last_day,
            or_(Schedule.end_date.is_(None), Schedule.end_date >= first_day),
        )
        if not include_archived:
            stmt = stmt.where(Medication.archived_at.is_(None))
        return [_to_rule(s, m) for s, m in (await self._session.execute(stmt)).tuples().all()]

    async def rules_for_schedules(
        self, patient_id: uuid.UUID, schedule_ids: Collection[uuid.UUID]
    ) -> list[ScheduleRule]:
        """Los horarios pedidos que pertenecen al paciente (archivados incluidos)."""
        stmt = _rules_stmt(patient_id).where(Schedule.id.in_(schedule_ids))
        return [_to_rule(s, m) for s, m in (await self._session.execute(stmt)).tuples().all()]
