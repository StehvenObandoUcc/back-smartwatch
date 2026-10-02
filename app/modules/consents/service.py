import uuid
from dataclasses import dataclass

from sqlalchemy import Row
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ForbiddenError
from app.core.security import utcnow
from app.modules.consents.models import PURPOSES, ConsentEvent
from app.modules.consents.repository import ConsentRepository
from app.modules.consents.schemas import ConsentActorOut, ConsentListOut, ConsentOut, Purpose

_CONSENT_DETAIL = {
    "health_data": "El paciente no ha dado el consentimiento para tratar sus datos de salud.",
    "ai_chat": "El paciente no ha dado el consentimiento para el chat con IA.",
}


@dataclass(frozen=True, slots=True)
class Subject:
    """Titular de los consentimientos: un paciente o (para cuidadores) el propio usuario."""

    user_id: uuid.UUID | None = None
    patient_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class Actor:
    user_id: uuid.UUID
    display_name: str
    on_behalf: bool


class ConsentService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = ConsentRepository(session)

    async def list(self, subject: Subject) -> ConsentListOut:
        rows = await self._repo.latest(user_id=subject.user_id, patient_id=subject.patient_id)
        return _to_list(rows)

    async def require(self, patient_id: uuid.UUID, purpose: str) -> None:
        if not await self._repo.is_granted(patient_id, purpose):
            raise ForbiddenError("consent_required", _CONSENT_DETAIL[purpose])

    async def require_health_data(self, patient_id: uuid.UUID) -> None:
        await self.require(patient_id, "health_data")

    async def set(
        self, subject: Subject, purpose: Purpose, *, granted: bool, version: str, actor: Actor
    ) -> ConsentOut:
        event = ConsentEvent(
            subject_user_id=subject.user_id,
            subject_patient_id=subject.patient_id,
            purpose=purpose,
            granted=granted,
            version=version,
            actor_user_id=actor.user_id,
            on_behalf=actor.on_behalf,
            created_at=utcnow(),
        )
        self._repo.add(event)
        await self._session.commit()
        return _to_out(event, actor.display_name)


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
