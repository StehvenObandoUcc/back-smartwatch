import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, CreatedAt, UuidPk


class OutboxMessage(UuidPk, CreatedAt, Base):
    """Mensaje pendiente de enviar (patrón outbox): se guarda en la misma transacción que lo causa.

    `dedupe_key` hace idempotente el encolado. El worker lo envía con reintentos y backoff.
    `payload` son los parámetros de la plantilla (`kind`), no el texto final.
    """

    __tablename__ = "notifications_outbox"
    __table_args__ = (
        UniqueConstraint("dedupe_key"),
        CheckConstraint("channel IN ('email', 'telegram')", name="channel"),
        CheckConstraint("status IN ('pending', 'sent', 'failed')", name="status"),
        # El worker solo mira lo pendiente.
        Index(
            "ix_notifications_outbox_due",
            "next_attempt_at",
            postgresql_where=text("status = 'pending'"),
        ),
    )

    dedupe_key: Mapped[str] = mapped_column(String(200))
    channel: Mapped[str] = mapped_column(String(16))
    kind: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(8), server_default="pending")
    attempts: Mapped[int] = mapped_column(server_default="0")
    next_attempt_at: Mapped[datetime] = mapped_column(server_default=func.now())
    last_error: Mapped[str | None] = mapped_column(String(500))
    sent_at: Mapped[datetime | None]


class TelegramLink(UuidPk, CreatedAt, Base):
    """Chat privado de Telegram vinculado a un usuario. Un chat pertenece a un solo usuario."""

    __tablename__ = "telegram_links"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True
    )
    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    username: Mapped[str | None] = mapped_column(String(64))


class NotificationPreference(Base):
    """Qué avisos llegan por qué canal. Sin fila = todo activado."""

    __tablename__ = "notification_preferences"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    missed_dose_email: Mapped[bool] = mapped_column(server_default="true")
    missed_dose_telegram: Mapped[bool] = mapped_column(server_default="true")
    weekly_report_email: Mapped[bool] = mapped_column(server_default="true")
    weekly_report_telegram: Mapped[bool] = mapped_column(server_default="true")
