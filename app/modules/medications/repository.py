import uuid
from datetime import date

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import Cursor, after_cursor
from app.modules.medications.models import Medication, Schedule
from app.modules.patients.models import Patient


class MedicationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, patient_id: uuid.UUID, medication_id: uuid.UUID) -> Medication | None:
        result = await self._session.execute(
            select(Medication).where(
                Medication.id == medication_id, Medication.patient_id == patient_id
            )
        )
        return result.scalar_one_or_none()

    async def get_by_client_id(
        self, patient_id: uuid.UUID, client_id: uuid.UUID
    ) -> Medication | None:
        result = await self._session.execute(
            select(Medication).where(
                Medication.patient_id == patient_id, Medication.client_id == client_id
            )
        )
        return result.scalar_one_or_none()

    async def active_with_schedules(
        self, patient_id: uuid.UUID, today: date, limit: int
    ) -> list[tuple[Medication, list[Schedule]]]:
        """Medicamentos no archivados con sus horarios vigentes (sin fin o que acaban hoy o
        después); un medicamento sin ninguno va con la lista vacía."""
        current = and_(
            Schedule.medication_id == Medication.id,
            or_(Schedule.end_date.is_(None), Schedule.end_date >= today),
        )
        meds = (
            select(Medication.id)
            .where(Medication.patient_id == patient_id, Medication.archived_at.is_(None))
            .order_by(Medication.created_at, Medication.id)
            .limit(limit)
            .scalar_subquery()
        )
        rows = await self._session.execute(
            select(Medication, Schedule)
            .outerjoin(Schedule, current)
            .where(Medication.id.in_(meds))
            .order_by(Medication.created_at, Medication.id, Schedule.created_at)
        )
        grouped: dict[uuid.UUID, tuple[Medication, list[Schedule]]] = {}
        for medication, schedule in rows.tuples():
            _, schedules = grouped.setdefault(medication.id, (medication, []))
            if schedule is not None:
                schedules.append(schedule)
        return list(grouped.values())

    async def list(
        self, patient_id: uuid.UUID, include_archived: bool, cursor: Cursor | None, limit: int
    ) -> list[Medication]:
        stmt = select(Medication).where(Medication.patient_id == patient_id)
        if not include_archived:
            stmt = stmt.where(Medication.archived_at.is_(None))
        condition = after_cursor(Medication.created_at, Medication.id, cursor)
        if condition is not None:
            stmt = stmt.where(condition)
        stmt = stmt.order_by(Medication.created_at, Medication.id).limit(limit + 1)
        return list((await self._session.execute(stmt)).scalars())

    async def bump_plan_version(self, patient_id: uuid.UUID) -> None:
        # Atómico: dos cambios simultáneos no pierden ninguna subida de versión.
        await self._session.execute(
            update(Patient)
            .where(Patient.id == patient_id)
            .values(plan_version=Patient.plan_version + 1)
        )

    def add(self, entity: Medication | Schedule) -> None:
        self._session.add(entity)


class ScheduleRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list(self, medication_id: uuid.UUID) -> list[Schedule]:
        result = await self._session.execute(
            select(Schedule)
            .where(Schedule.medication_id == medication_id)
            .order_by(Schedule.created_at, Schedule.id)
        )
        return list(result.scalars())

    async def get(self, medication_id: uuid.UUID, schedule_id: uuid.UUID) -> Schedule | None:
        result = await self._session.execute(
            select(Schedule).where(
                Schedule.id == schedule_id, Schedule.medication_id == medication_id
            )
        )
        return result.scalar_one_or_none()

    async def count(self, medication_id: uuid.UUID) -> int:
        result = await self._session.execute(
            select(func.count())
            .select_from(Schedule)
            .where(Schedule.medication_id == medication_id)
        )
        return int(result.scalar_one())

    async def delete(self, medication_id: uuid.UUID, schedule_id: uuid.UUID) -> bool:
        result = await self._session.execute(
            delete(Schedule)
            .where(Schedule.id == schedule_id, Schedule.medication_id == medication_id)
            .returning(Schedule.id)
        )
        return result.first() is not None
