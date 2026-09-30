import uuid

from sqlalchemy import CheckConstraint, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, CreatedAt, UuidPk

PURPOSES = ("health_data", "ai_chat", "notifications")


class ConsentEvent(UuidPk, CreatedAt, Base):
    """Historial de decisiones (solo inserciones). El estado vigente es el último por finalidad.

    El titular es un paciente (sus datos de salud) o, para cuidadores, el propio usuario.
    """

    __tablename__ = "consent_events"
    __table_args__ = (
        CheckConstraint("purpose IN ('health_data', 'ai_chat', 'notifications')", name="purpose"),
        CheckConstraint(
            "(subject_user_id IS NULL) <> (subject_patient_id IS NULL)", name="single_subject"
        ),
        Index("ix_consent_events_user_purpose", "subject_user_id", "purpose", "created_at"),
        Index("ix_consent_events_patient_purpose", "subject_patient_id", "purpose", "created_at"),
    )

    subject_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE")
    )
    subject_patient_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("patients.id", ondelete="CASCADE")
    )
    purpose: Mapped[str] = mapped_column(String(32))
    granted: Mapped[bool]
    version: Mapped[str] = mapped_column(String(32))
    actor_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    on_behalf: Mapped[bool] = mapped_column(default=False)
