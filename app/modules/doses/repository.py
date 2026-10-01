import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.doses.history import EventRecord
from app.modules.doses.models import DoseEvent
from app.modules.medications.models import Medication


class DoseEventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def insert_if_new(self, event: DoseEvent) -> bool:
        """Guarda el evento; False si ya existía ese `event_id` o esa dosis (sin carreras)."""
        stmt = (
            insert(DoseEvent)
            .values(
                event_id=event.event_id,
                patient_id=event.patient_id,
                device_id=event.device_id,
                schedule_id=event.schedule_id,
                medication_id=event.medication_id,
                scheduled_at=event.scheduled_at,
                status=event.status,
                acted_at=event.acted_at,
            )
            .on_conflict_do_nothing()
            .returning(DoseEvent.id)
        )
        return (await self._session.execute(stmt)).first() is not None

    async def event_exists(self, event_id: uuid.UUID) -> bool:
        result = await self._session.execute(
            select(DoseEvent.id).where(DoseEvent.event_id == event_id)
        )
        return result.first() is not None

    async def in_range(
        self, patient_id: uuid.UUID, start: datetime, end: datetime
    ) -> list[EventRecord]:
        """Eventos con `scheduled_at` en [start, end), con los datos del medicamento."""
        stmt = (
            select(DoseEvent, Medication.name, Medication.dosage)
            .join(Medication, Medication.id == DoseEvent.medication_id)
            .where(
                DoseEvent.patient_id == patient_id,
                DoseEvent.scheduled_at >= start,
                DoseEvent.scheduled_at < end,
            )
        )
        return [
            EventRecord(
                schedule_id=event.schedule_id,
                medication_id=event.medication_id,
                medication_name=name,
                dosage=dosage,
                scheduled_at=event.scheduled_at,
                status=event.status,
                acted_at=event.acted_at,
            )
            for event, name, dosage in (await self._session.execute(stmt)).all()
        ]
