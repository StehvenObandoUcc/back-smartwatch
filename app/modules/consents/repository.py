import uuid

from sqlalchemy import Row, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.consents.models import ConsentEvent
from app.modules.users.models import User


class ConsentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def latest_for_user(self, user_id: uuid.UUID) -> list[Row[ConsentEvent, str]]:
        """Última decisión de cada finalidad, con el nombre de quien la tomó."""
        stmt = (
            select(ConsentEvent, User.display_name)
            .join(User, User.id == ConsentEvent.actor_user_id)
            .where(ConsentEvent.subject_user_id == user_id)
            .order_by(ConsentEvent.purpose, ConsentEvent.created_at.desc())
            .distinct(ConsentEvent.purpose)
        )
        result = await self._session.execute(stmt)
        return list(result.all())

    def add(self, event: ConsentEvent) -> None:
        self._session.add(event)
