import uuid

from sqlalchemy import CheckConstraint, Index, SmallInteger, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, CreatedAt, UuidPk


class HealthDataAccess(UuidPk, CreatedAt, Base):
    """Quién accedió a los datos de salud de qué paciente (solo inserciones).

    `actor_id` y `patient_id` NO son claves foráneas a propósito: el rastro debe sobrevivir al
    borrado de la cuenta del actor y al del paciente. Nunca guarda contenido, solo el hecho del
    acceso (recurso, método y estado).
    """

    __tablename__ = "health_data_access_log"
    __table_args__ = (
        CheckConstraint("actor_type IN ('user', 'device')", name="actor_type"),
        CheckConstraint("action IN ('read', 'write')", name="action"),
    )

    actor_type: Mapped[str] = mapped_column(String(8))
    actor_id: Mapped[uuid.UUID]
    patient_id: Mapped[uuid.UUID]
    action: Mapped[str] = mapped_column(String(8))
    # medications, schedules, plan, dose_events, dose_history, adherence, reports, chat,
    # patient, consents, caregivers, devices.
    resource: Mapped[str] = mapped_column(String(32))
    method: Mapped[str] = mapped_column(String(8))
    status: Mapped[int] = mapped_column(SmallInteger)
    request_id: Mapped[str | None] = mapped_column(String(64))


# Consultar el rastro de un paciente: lo más reciente primero.
Index(
    "ix_health_data_access_log_patient_created",
    HealthDataAccess.patient_id,
    HealthDataAccess.created_at.desc(),
)
