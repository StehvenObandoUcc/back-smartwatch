import uuid
from datetime import date

from sqlalchemy import func, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.chat.models import ChatUsage


class ChatUsageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def reserve(self, principal_id: uuid.UUID, day: date, limit: int) -> int | None:
        """Reserva un mensaje del día de forma atómica: el total tras reservar, o None si ya se
        alcanzó el límite. Dos peticiones simultáneas no pueden pasarse del límite."""
        stmt = (
            insert(ChatUsage)
            .values(principal_id=principal_id, day=day, messages=1)
            .on_conflict_do_update(
                index_elements=["principal_id", "day"],
                set_={"messages": ChatUsage.messages + 1},
                where=ChatUsage.messages < limit,
            )
            .returning(ChatUsage.messages)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def release(self, principal_id: uuid.UUID, day: date) -> None:
        await self._session.execute(
            update(ChatUsage)
            .where(ChatUsage.principal_id == principal_id, ChatUsage.day == day)
            .values(messages=func.greatest(ChatUsage.messages - 1, 0))
        )

    async def add_tokens(
        self, principal_id: uuid.UUID, day: date, input_tokens: int, output_tokens: int
    ) -> None:
        await self._session.execute(
            update(ChatUsage)
            .where(ChatUsage.principal_id == principal_id, ChatUsage.day == day)
            .values(
                input_tokens=ChatUsage.input_tokens + input_tokens,
                output_tokens=ChatUsage.output_tokens + output_tokens,
            )
        )
