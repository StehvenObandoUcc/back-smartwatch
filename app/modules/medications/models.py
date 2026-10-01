import uuid
from datetime import date, datetime

from sqlalchemy import ForeignKey, Index, SmallInteger, String, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, CreatedAt, UuidPk


class Medication(UuidPk, CreatedAt, Base):
    """Medicamento de un paciente. Se archiva (`archived_at`): conserva el historial."""

    __tablename__ = "medications"
    __table_args__ = (Index("ix_medications_patient_created", "patient_id", "created_at", "id"),)

    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patients.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(120))
    dosage: Mapped[str] = mapped_column(String(80))
    instructions: Mapped[str | None] = mapped_column(String(500))
    color: Mapped[str] = mapped_column(String(7))
    archived_at: Mapped[datetime | None]
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())

    @property
    def archived(self) -> bool:
        return self.archived_at is not None


class Schedule(UuidPk, CreatedAt, Base):
    """Horario simple: horas del día por días de la semana entre dos fechas locales del paciente."""

    __tablename__ = "schedules"

    medication_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("medications.id", ondelete="CASCADE"), index=True
    )
    times: Mapped[list[str]] = mapped_column(ARRAY(String(5)))  # "HH:MM", ordenadas
    days_of_week: Mapped[list[int]] = mapped_column(ARRAY(SmallInteger))  # ISO, 1 = lunes
    start_date: Mapped[date]
    end_date: Mapped[date | None]
    # Desde cuándo cuentan sus dosis para el historial (se reinicia al editar el horario): lo
    # anterior no se puede marcar como omitido con un horario que no existía.
    effective_from: Mapped[datetime] = mapped_column(server_default=func.now())
