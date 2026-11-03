import uuid
from typing import Any

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.notifications.models import NotificationPreference, OutboxMessage, TelegramLink


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

    async def delete_unsent_for(self, *, email: str, chat_id: int | None) -> int:
        """Borra los mensajes sin enviar (`pending` o `failed`) dirigidos a ese correo o chat.

        Los `sent` ya no llevan datos (el worker limpia el payload al enviarlos). No hace commit.
        """
        recipient = and_(
            OutboxMessage.channel == "email",
            func.lower(OutboxMessage.payload["to"].astext) == email.lower(),
        )
        if chat_id is not None:
            recipient = or_(
                recipient,
                and_(
                    OutboxMessage.channel == "telegram",
                    OutboxMessage.payload["chat_id"].astext == str(chat_id),
                ),
            )
        result = await self._session.execute(
            delete(OutboxMessage)
            .where(OutboxMessage.status.in_(("pending", "failed")), recipient)
            .returning(OutboxMessage.id)
        )
        return len(result.all())


class TelegramLinkRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_for_user(self, user_id: uuid.UUID) -> TelegramLink | None:
        result = await self._session.execute(
            select(TelegramLink).where(TelegramLink.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def replace(self, user_id: uuid.UUID, chat_id: int, username: str | None) -> None:
        """Vincula el chat al usuario; suelta el chat de otro usuario y el chat anterior de éste."""
        await self._session.execute(
            delete(TelegramLink).where(
                or_(TelegramLink.user_id == user_id, TelegramLink.chat_id == chat_id)
            )
        )
        self._session.add(TelegramLink(user_id=user_id, chat_id=chat_id, username=username))
        await self._session.flush()

    async def delete_for_user(self, user_id: uuid.UUID) -> None:
        await self._session.execute(delete(TelegramLink).where(TelegramLink.user_id == user_id))

    async def delete_for_chat(self, chat_id: int) -> bool:
        result = await self._session.execute(
            delete(TelegramLink).where(TelegramLink.chat_id == chat_id).returning(TelegramLink.id)
        )
        return result.first() is not None


class PreferenceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, user_id: uuid.UUID) -> NotificationPreference | None:
        return await self._session.get(NotificationPreference, user_id)

    async def upsert(self, user_id: uuid.UUID, **values: bool) -> None:
        stmt = insert(NotificationPreference).values(user_id=user_id, **values)
        stmt = stmt.on_conflict_do_update(index_elements=["user_id"], set_=values)
        await self._session.execute(stmt)
