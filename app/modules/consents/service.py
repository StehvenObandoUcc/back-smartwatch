import uuid

from sqlalchemy import Row
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import utcnow
from app.modules.consents.models import PURPOSES, ConsentEvent
from app.modules.consents.repository import ConsentRepository
from app.modules.consents.schemas import ConsentActorOut, ConsentListOut, ConsentOut, Purpose


class ConsentService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = ConsentRepository(session)

    async def list_for_user(self, user_id: uuid.UUID) -> ConsentListOut:
        return _to_list(await self._repo.latest_for_user(user_id))

    async def set_for_user(
        self, user_id: uuid.UUID, purpose: Purpose, *, granted: bool, version: str, actor_name: str
    ) -> ConsentOut:
        event = ConsentEvent(
            subject_user_id=user_id,
            purpose=purpose,
            granted=granted,
            version=version,
            actor_user_id=user_id,
            on_behalf=False,
            created_at=utcnow(),
        )
        self._repo.add(event)
        await self._session.commit()
        return _to_out(event, actor_name)


def _to_out(event: ConsentEvent, actor_name: str) -> ConsentOut:
    return ConsentOut(
        purpose=event.purpose,
        granted=event.granted,
        version=event.version,
        updated_at=event.created_at,
        granted_by=ConsentActorOut(
            user_id=event.actor_user_id, display_name=actor_name, on_behalf=event.on_behalf
        ),
    )


def _to_list(rows: list[Row[ConsentEvent, str]]) -> ConsentListOut:
    """Siempre las tres finalidades; las no respondidas, sin conceder."""
    latest = {event.purpose: _to_out(event, name) for event, name in rows}
    return ConsentListOut(
        items=[
            latest.get(purpose)
            or ConsentOut(
                purpose=purpose,
                granted=False,
                version=None,
                updated_at=None,
                granted_by=None,
            )
            for purpose in PURPOSES
        ]
    )
