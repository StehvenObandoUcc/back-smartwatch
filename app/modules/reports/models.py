import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    LargeBinary,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, CreatedAt, UuidPk


class Report(UuidPk, CreatedAt, Base):
    """Reporte de adherencia de 7 días. Se genera en el worker: nace `pending`.

    Un solo reporte por paciente y último día del periodo. El PDF se guarda en la fila (unos
    pocos KB), así que no hace falta un almacén de archivos.
    """

    __tablename__ = "reports"
    __table_args__ = (
        UniqueConstraint("patient_id", "period_end"),
        CheckConstraint("trigger IN ('weekly', 'manual')", name="trigger"),
        CheckConstraint("status IN ('pending', 'ready', 'failed')", name="status"),
        Index("ix_reports_patient_created", "patient_id", "created_at", "id"),
        # El worker solo mira lo pendiente.
        Index("ix_reports_pending", "created_at", postgresql_where=text("status = 'pending'")),
    )

    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patients.id", ondelete="CASCADE"))
    period_start: Mapped[date]
    period_end: Mapped[date]
    trigger: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(8), server_default="pending")
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    pdf: Mapped[bytes | None] = mapped_column(LargeBinary)
    generated_at: Mapped[datetime | None]
