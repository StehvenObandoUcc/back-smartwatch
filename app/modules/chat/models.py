import uuid
from datetime import date

from sqlalchemy import BigInteger
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class ChatUsage(Base):
    """Uso diario del chat por usuario (web) o por reloj. Solo contadores: nunca el texto."""

    __tablename__ = "chat_usage"

    principal_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    day: Mapped[date] = mapped_column(primary_key=True)  # día local del paciente
    messages: Mapped[int] = mapped_column(server_default="0")
    input_tokens: Mapped[int] = mapped_column(BigInteger, server_default="0")
    output_tokens: Mapped[int] = mapped_column(BigInteger, server_default="0")
