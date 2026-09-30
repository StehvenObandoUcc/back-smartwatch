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

    def add(self, event: ConsentEvent) -> None:
        self._session.add(event)
