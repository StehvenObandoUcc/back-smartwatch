import uuid
from dataclasses import dataclass

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.models import HealthDataAccess


@dataclass(frozen=True, slots=True)
class AccessEntry:
    actor_type: str
    actor_id: uuid.UUID
    patient_id: uuid.UUID
    action: str
    resource: str
    method: str
    status: int
    request_id: str | None


class AccessLogRepository:
    """Solo inserciones: el registro de accesos no se modifica ni se borra desde la API."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, entry: AccessEntry) -> None:
        """Inserta el acceso. No hace commit."""
        await self._session.execute(
            insert(HealthDataAccess).values(
                actor_type=entry.actor_type,
                actor_id=entry.actor_id,
                patient_id=entry.patient_id,
                action=entry.action,
                resource=entry.resource,
                method=entry.method,
                status=entry.status,
                request_id=entry.request_id,
            )
        )
