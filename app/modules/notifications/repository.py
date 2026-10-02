from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.notifications.models import OutboxMessage


class OutboxRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def enqueue(
        self, *, channel: str, kind: str, payload: dict[str, Any], dedupe_key: str
    ) -> bool:
        """Encola el mensaje; False si ya existía uno con esa `dedupe_key`. No hace commit."""
        stmt = (
            insert(OutboxMessage)
            .values(channel=channel, kind=kind, payload=payload, dedupe_key=dedupe_key)
            .on_conflict_do_nothing(index_elements=["dedupe_key"])
            .returning(OutboxMessage.id)
        )
        return (await self._session.execute(stmt)).first() is not None
