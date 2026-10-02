import uuid

from sqlalchemy import Row, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.consents.models import ConsentEvent
from app.modules.users.models import User


class ConsentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def latest(
        self, *, user_id: uuid.UUID | None = None, patient_id: uuid.UUID | None = None
    ) -> list[Row[ConsentEvent, str]]:
        """Última decisión de cada finalidad de un titular, con el nombre de quien la tomó."""
        subject = (
            ConsentEvent.subject_user_id == user_id
            if user_id is not None
            else ConsentEvent.subject_patient_id == patient_id
        )
        stmt = (
            select(ConsentEvent, User.display_name)
            .join(User, User.id == ConsentEvent.actor_user_id)
            .where(subject)
            .order_by(ConsentEvent.purpose, ConsentEvent.created_at.desc())
            .distinct(ConsentEvent.purpose)
        )
        result = await self._session.execute(stmt)
        return list(result.all())

    async def is_granted(self, patient_id: uuid.UUID, purpose: str) -> bool:
        """Última decisión del paciente para esa finalidad (sin decisión = no concedido)."""
        result = await self._session.execute(
            select(ConsentEvent.granted)
            .where(ConsentEvent.subject_patient_id == patient_id, ConsentEvent.purpose == purpose)
            .order_by(ConsentEvent.created_at.desc())
            .limit(1)
        )
        return bool(result.scalar_one_or_none())

    async def is_granted_for_user(self, user_id: uuid.UUID, purpose: str) -> bool:
        """Última decisión del propio usuario (cuidadores) para esa finalidad."""
        result = await self._session.execute(
            select(ConsentEvent.granted)
            .where(ConsentEvent.subject_user_id == user_id, ConsentEvent.purpose == purpose)
            .order_by(ConsentEvent.created_at.desc())
            .limit(1)
        )
        return bool(result.scalar_one_or_none())

    def add(self, event: ConsentEvent) -> None:
        self._session.add(event)
