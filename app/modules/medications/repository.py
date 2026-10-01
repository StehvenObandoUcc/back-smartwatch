import uuid

from sqlalchemy import delete, func, select, update
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
