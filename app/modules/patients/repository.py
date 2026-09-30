import uuid
from datetime import datetime

from sqlalchemy import Exists, Row, exists, func, or_, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import Cursor, after_cursor
from app.modules.patients.models import CaregiverInvitation, CareLink, Patient
from app.modules.users.models import User


def _has_active_link(user_id: uuid.UUID) -> Exists:
    return exists().where(
        CareLink.patient_id == Patient.id,
        CareLink.caregiver_user_id == user_id,
        CareLink.active.is_(True),
    )


class PatientRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_accessible(self, patient_id: uuid.UUID, user_id: uuid.UUID) -> Patient | None:
        """El paciente si el usuario es su dueño o un cuidador con vínculo activo."""
        result = await self._session.execute(
            select(Patient).where(
                Patient.id == patient_id,
                or_(Patient.owner_user_id == user_id, _has_active_link(user_id)),
            )
        )
        return result.scalar_one_or_none()

    async def lock(self, patient_id: uuid.UUID) -> None:
        """Bloquea la fila del paciente para serializar cambios en sus vínculos."""
        await self._session.execute(
            select(Patient.id).where(Patient.id == patient_id).with_for_update()
        )

    async def get_owned_id(self, user_id: uuid.UUID) -> uuid.UUID | None:
        result = await self._session.execute(
            select(Patient.id).where(Patient.owner_user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def list_accessible(
        self, user_id: uuid.UUID, cursor: Cursor | None, limit: int
    ) -> list[Patient]:
        # Primero los IDs accesibles (dueño + vínculos activos, cada rama con su índice) y luego
        # los pacientes: con un OR sobre `patients` Postgres recorre la tabla entera.
        accessible_ids = union_all(
            select(Patient.id).where(Patient.owner_user_id == user_id),
            select(CareLink.patient_id).where(
                CareLink.caregiver_user_id == user_id, CareLink.active.is_(True)
            ),
        )
        stmt = select(Patient).where(Patient.id.in_(accessible_ids))
        condition = after_cursor(Patient.created_at, Patient.id, cursor)
        if condition is not None:
            stmt = stmt.where(condition)
        stmt = stmt.order_by(Patient.created_at, Patient.id).limit(limit + 1)
        return list((await self._session.execute(stmt)).scalars())

    def add(self, entity: Patient | CareLink | CaregiverInvitation) -> None:
        self._session.add(entity)


class CareLinkRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, patient_id: uuid.UUID, caregiver_id: uuid.UUID) -> CareLink | None:
        result = await self._session.execute(
            select(CareLink)
            .where(CareLink.patient_id == patient_id, CareLink.caregiver_user_id == caregiver_id)
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def count_active(self, patient_id: uuid.UUID) -> int:
        result = await self._session.execute(
            select(func.count())
            .select_from(CareLink)
            .where(CareLink.patient_id == patient_id, CareLink.active.is_(True))
        )
        return int(result.scalar_one())

    async def list_active(
        self, patient_id: uuid.UUID, cursor: Cursor | None, limit: int
    ) -> list[Row[CareLink, str]]:
        stmt = (
            select(CareLink, User.display_name)
            .join(User, User.id == CareLink.caregiver_user_id)
            .where(CareLink.patient_id == patient_id, CareLink.active.is_(True))
        )
        condition = after_cursor(CareLink.created_at, CareLink.id, cursor)
        if condition is not None:
            stmt = stmt.where(condition)
        stmt = stmt.order_by(CareLink.created_at, CareLink.id).limit(limit + 1)
        return list((await self._session.execute(stmt)).all())


class InvitationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_usable_for_update(
        self, code_hash: str, now: datetime
    ) -> CaregiverInvitation | None:
        result = await self._session.execute(
            select(CaregiverInvitation)
            .where(
                CaregiverInvitation.code_hash == code_hash,
                CaregiverInvitation.used_at.is_(None),
                CaregiverInvitation.expires_at > now,
            )
            .with_for_update()
        )
        return result.scalar_one_or_none()
