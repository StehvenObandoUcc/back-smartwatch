import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, UuidPk


class DoseEvent(UuidPk, Base):
    """Toma o dosis omitida por el paciente, registrada desde el reloj.

    `event_id` (generado por el reloj) hace idempotente el reintento; (`schedule_id`,
    `scheduled_at`) impide dos eventos para la misma dosis. `schedule_id` no es clave foránea:
    los horarios se borran de verdad y el evento debe sobrevivir con su historial.
    """

    __tablename__ = "dose_events"
    __table_args__ = (
        UniqueConstraint("event_id"),
        UniqueConstraint("schedule_id", "scheduled_at"),
        CheckConstraint("status IN ('TAKEN', 'SKIPPED')", name="status"),
        # Historial y adherencia: eventos de un paciente en un rango de fechas.
        Index("ix_dose_events_patient_scheduled", "patient_id", "scheduled_at"),
    )

    event_id: Mapped[uuid.UUID]
    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patients.id", ondelete="CASCADE"))
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("devices.id", ondelete="SET NULL")
    )
    schedule_id: Mapped[uuid.UUID]
    medication_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("medications.id", ondelete="CASCADE")
    )
    scheduled_at: Mapped[datetime]
    status: Mapped[str] = mapped_column(String(8))
    acted_at: Mapped[datetime]
    received_at: Mapped[datetime] = mapped_column(server_default=func.now())
