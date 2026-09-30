import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, CreatedAt, UuidPk


class RefreshToken(UuidPk, CreatedAt, Base):
    """Refresh token opaco (solo se guarda su SHA-256). Pertenece a un usuario o a un reloj.

    Todos los tokens de una sesión comparten `family_id`. Al rotar, el anterior queda con
    `rotated_at`; si alguien lo vuelve a presentar, se revoca toda la familia.
    """

    __tablename__ = "refresh_tokens"
    __table_args__ = (
        CheckConstraint("(user_id IS NULL) <> (device_id IS NULL)", name="single_owner"),
    )

    family_id: Mapped[uuid.UUID] = mapped_column(index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # Sesión de reloj (la FK a devices la añade el módulo devices).
    device_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    expires_at: Mapped[datetime]
    rotated_at: Mapped[datetime | None]
    revoked_at: Mapped[datetime | None]
