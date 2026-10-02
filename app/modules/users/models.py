from datetime import datetime

from sqlalchemy import CheckConstraint, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, CreatedAt, UuidPk

ROLES = ("patient", "caregiver")


class User(UuidPk, CreatedAt, Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role IN ('patient', 'caregiver')", name="role"),)

    # Siempre en minúsculas: la unicidad no depende de mayúsculas.
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(80))
    role: Mapped[str] = mapped_column(String(16))
    timezone: Mapped[str] = mapped_column(String(64))
    locale: Mapped[str] = mapped_column(String(8), default="es")
    email_verified_at: Mapped[datetime | None]
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
