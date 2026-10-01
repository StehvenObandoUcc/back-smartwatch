import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, CreatedAt, UuidPk


class Device(UuidPk, CreatedAt, Base):
    """Reloj vinculado a un paciente. Al desvincularlo se marca `revoked_at` (no se borra)."""

    __tablename__ = "devices"
    __table_args__ = (
        # Un solo reloj activo por paciente.
        Index(
            "uq_devices_active_patient",
            "patient_id",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
        Index("ix_devices_patient_created", "patient_id", "created_at", "id"),
    )

    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patients.id", ondelete="CASCADE"))
    model: Mapped[str] = mapped_column(String(80))
    app_version: Mapped[str | None] = mapped_column(String(32))
    paired_at: Mapped[datetime]
    last_seen_at: Mapped[datetime | None]
    push_token: Mapped[str | None] = mapped_column(String(4096))
    revoked_at: Mapped[datetime | None]


class PairingCode(UuidPk, CreatedAt, Base):
    """Estado de una vinculación en curso (flujo tipo device code). Solo hashes de los códigos."""

    __tablename__ = "pairing_codes"

    user_code_hash: Mapped[str] = mapped_column(String(64), unique=True)
    device_code_hash: Mapped[str] = mapped_column(String(64), unique=True)
    model: Mapped[str] = mapped_column(String(80))
    app_version: Mapped[str | None] = mapped_column(String(32))
    expires_at: Mapped[datetime]
    interval_seconds: Mapped[int]
    last_polled_at: Mapped[datetime | None]
    confirmed_at: Mapped[datetime | None]
    confirmed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE")
    )
    consumed_at: Mapped[datetime | None]
