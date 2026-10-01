"""Medicamentos y horarios de un paciente.

Toda operación exige acceso al paciente (404 si no) y su consentimiento `health_data` (403).
Cada cambio sube la versión del plan (`patients.plan_version`) en la misma transacción.
"""

import uuid
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser
from app.core.errors import NotFoundError, UnprocessableError
from app.core.pagination import Page, next_cursor
from app.core.security import utcnow
from app.modules.medications.models import Medication, Schedule
from app.modules.medications.repository import MedicationRepository, ScheduleRepository
from app.modules.medications.schemas import (
    MAX_SCHEDULES_PER_MEDICATION,
    MedicationCreate,
    MedicationOut,
    MedicationPage,
    MedicationUpdate,
    ScheduleCreate,
    ScheduleList,
    ScheduleOut,
    ScheduleUpdate,
)
from app.modules.patients.service import PatientService


def medication_not_found() -> NotFoundError:
    return NotFoundError("medication_not_found", "El medicamento no existe.")


def schedule_not_found() -> NotFoundError:
    return NotFoundError("schedule_not_found", "El horario no existe.")


def to_medication_out(medication: Medication) -> MedicationOut:
    return MedicationOut(
        id=medication.id,
        patient_id=medication.patient_id,
        name=medication.name,
        dosage=medication.dosage,
        instructions=medication.instructions,
        color=medication.color,
        archived=medication.archived,
        created_at=medication.created_at,
        updated_at=medication.updated_at,
    )


def to_schedule_out(schedule: Schedule) -> ScheduleOut:
    return ScheduleOut(
        id=schedule.id,
        medication_id=schedule.medication_id,
        times=list(schedule.times),
        days_of_week=list(schedule.days_of_week),
        start_date=schedule.start_date,
        end_date=schedule.end_date,
    )


def _check_range(start: date, end: date | None) -> None:
    if end is not None and end < start:
        raise UnprocessableError(["body", "endDate"], "endDate no puede ser anterior a startDate")


class MedicationService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._patients = PatientService(session)
        self._medications = MedicationRepository(session)
        self._schedules = ScheduleRepository(session)

    async def _medication(
        self, user: CurrentUser, patient_id: uuid.UUID, medication_id: uuid.UUID
    ) -> Medication:
        await self._patients.health_access(user, patient_id)
        medication = await self._medications.get(patient_id, medication_id)
        if medication is None:
            raise medication_not_found()
        return medication

    # ─── Medicamentos ─────────────────────────────────────────────────────────

    async def list(
        self, user: CurrentUser, patient_id: uuid.UUID, include_archived: bool, page: Page
    ) -> MedicationPage:
        await self._patients.health_access(user, patient_id)
        rows = await self._medications.list(patient_id, include_archived, page.cursor, page.limit)
        items, cursor = next_cursor(rows, page.limit, lambda m: (m.created_at, m.id))
        return MedicationPage(items=[to_medication_out(m) for m in items], next_cursor=cursor)

    async def create(
        self, user: CurrentUser, patient_id: uuid.UUID, data: MedicationCreate
    ) -> MedicationOut:
        await self._patients.health_access(user, patient_id)
        now = utcnow()
        medication = Medication(
            patient_id=patient_id,
            name=data.name,
            dosage=data.dosage,
            instructions=data.instructions,
            color=data.color,
            created_at=now,
            updated_at=now,
        )
        self._medications.add(medication)
        await self._medications.bump_plan_version(patient_id)
        await self._session.commit()
        return to_medication_out(medication)

    async def get(
        self, user: CurrentUser, patient_id: uuid.UUID, medication_id: uuid.UUID
    ) -> MedicationOut:
        return to_medication_out(await self._medication(user, patient_id, medication_id))

    async def update(
        self,
        user: CurrentUser,
        patient_id: uuid.UUID,
        medication_id: uuid.UUID,
        data: MedicationUpdate,
    ) -> MedicationOut:
        medication = await self._medication(user, patient_id, medication_id)
        for field in data.model_fields_set:
            setattr(medication, field, getattr(data, field))
        medication.updated_at = utcnow()
        await self._medications.bump_plan_version(patient_id)
        await self._session.commit()
        return to_medication_out(medication)

    async def archive(
        self, user: CurrentUser, patient_id: uuid.UUID, medication_id: uuid.UUID
    ) -> None:
        medication = await self._medication(user, patient_id, medication_id)
        if medication.archived:
            return
        now = utcnow()
        medication.archived_at = now
        medication.updated_at = now
        await self._medications.bump_plan_version(patient_id)
        await self._session.commit()

    # ─── Horarios ─────────────────────────────────────────────────────────────

    async def list_schedules(
        self, user: CurrentUser, patient_id: uuid.UUID, medication_id: uuid.UUID
    ) -> ScheduleList:
        await self._medication(user, patient_id, medication_id)
        rows = await self._schedules.list(medication_id)
        return ScheduleList(items=[to_schedule_out(s) for s in rows])

    async def create_schedule(
        self,
        user: CurrentUser,
        patient_id: uuid.UUID,
        medication_id: uuid.UUID,
        data: ScheduleCreate,
    ) -> ScheduleOut:
        await self._medication(user, patient_id, medication_id)
        if await self._schedules.count(medication_id) >= MAX_SCHEDULES_PER_MEDICATION:
            raise UnprocessableError(
                ["body"],
                f"Un medicamento admite como máximo {MAX_SCHEDULES_PER_MEDICATION} horarios",
            )
        now = utcnow()
        schedule = Schedule(
            medication_id=medication_id,
            times=data.times,
            days_of_week=data.days_of_week,
            start_date=data.start_date,
            end_date=data.end_date,
            created_at=now,
            effective_from=now,
        )
        self._medications.add(schedule)
        await self._medications.bump_plan_version(patient_id)
        await self._session.commit()
        return to_schedule_out(schedule)

    async def update_schedule(
        self,
        user: CurrentUser,
        patient_id: uuid.UUID,
        medication_id: uuid.UUID,
        schedule_id: uuid.UUID,
        data: ScheduleUpdate,
    ) -> ScheduleOut:
        await self._medication(user, patient_id, medication_id)
        schedule = await self._schedules.get(medication_id, schedule_id)
        if schedule is None:
            raise schedule_not_found()
        for field in data.model_fields_set:
            setattr(schedule, field, getattr(data, field))
        _check_range(schedule.start_date, schedule.end_date)
        schedule.effective_from = utcnow()
        await self._medications.bump_plan_version(patient_id)
        await self._session.commit()
        return to_schedule_out(schedule)

    async def delete_schedule(
        self,
        user: CurrentUser,
        patient_id: uuid.UUID,
        medication_id: uuid.UUID,
        schedule_id: uuid.UUID,
    ) -> None:
        await self._medication(user, patient_id, medication_id)
        if await self._schedules.delete(medication_id, schedule_id):
            await self._medications.bump_plan_version(patient_id)
        await self._session.commit()
