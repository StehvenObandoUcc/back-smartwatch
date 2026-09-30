import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, CreatedAt, UuidPk


class Patient(UuidPk, CreatedAt, Base):
    """Persona que toma la medicación. Con cuenta propia (`owner_user_id`) o gestionada."""

    __tablename__ = "patients"
    __table_args__ = (Index("ix_patients_created_at_id", "created_at", "id"),)

    display_name: Mapped[str] = mapped_column(String(80))
    timezone: Mapped[str] = mapped_column(String(64))
    # Cuenta del propio paciente; null en pacientes gestionados por cuidadores.
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True
    )
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE")
    )
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    @property
    def managed(self) -> bool:
        return self.owner_user_id is None


class CareLink(UuidPk, CreatedAt, Base):
    """Vínculo cuidador-paciente. No se borra: se desactiva y queda para auditoría."""

    __tablename__ = "care_links"
    __table_args__ = (
        UniqueConstraint("patient_id", "caregiver_user_id"),
        # Listar pacientes de un cuidador y comprobar acceso: (cuidador, activo).
        Index("ix_care_links_caregiver_active", "caregiver_user_id", "active"),
    )

    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patients.id", ondelete="CASCADE"))
    caregiver_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    active: Mapped[bool] = mapped_column(default=True)
    revoked_at: Mapped[datetime | None]
    revoked_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )


class CaregiverInvitation(UuidPk, CreatedAt, Base):
    """Código de un solo uso para vincular un cuidador. Solo se guarda su SHA-256."""

    __tablename__ = "caregiver_invitations"

    patient_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("patients.id", ondelete="CASCADE"), index=True
    )
    code_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE")
    )
    expires_at: Mapped[datetime]
    used_at: Mapped[datetime | None]
    used_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
