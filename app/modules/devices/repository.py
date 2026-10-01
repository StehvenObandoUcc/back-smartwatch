import uuid
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import Cursor, after_cursor
from app.modules.auth.models import RefreshToken
from app.modules.devices.models import Device, PairingCode


class DeviceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, device_id: uuid.UUID) -> Device | None:
        return await self._session.get(Device, device_id)

    async def get_active(self, device_id: uuid.UUID) -> Device | None:
        result = await self._session.execute(
            select(Device).where(Device.id == device_id, Device.revoked_at.is_(None))
        )
        return result.scalar_one_or_none()

    async def is_active(self, device_id: uuid.UUID) -> bool:
        result = await self._session.execute(
            select(Device.id).where(Device.id == device_id, Device.revoked_at.is_(None))
        )
        return result.first() is not None

    async def active_for_patient_for_update(self, patient_id: uuid.UUID) -> list[Device]:
        result = await self._session.execute(
            select(Device)
            .where(Device.patient_id == patient_id, Device.revoked_at.is_(None))
            .with_for_update()
        )
        return list(result.scalars())

    async def list_active(
        self, patient_id: uuid.UUID, cursor: Cursor | None, limit: int
    ) -> list[Device]:
        stmt = select(Device).where(Device.patient_id == patient_id, Device.revoked_at.is_(None))
        condition = after_cursor(Device.created_at, Device.id, cursor)
        if condition is not None:
            stmt = stmt.where(condition)
        stmt = stmt.order_by(Device.created_at, Device.id).limit(limit + 1)
        return list((await self._session.execute(stmt)).scalars())

    async def revoke_tokens(self, device_id: uuid.UUID, now: datetime) -> None:
        await self._session.execute(
            update(RefreshToken)
            .where(RefreshToken.device_id == device_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )

    def add(self, entity: Device | PairingCode) -> None:
        self._session.add(entity)


class PairingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_pending_by_user_code(
        self, user_code_hash: str, now: datetime
    ) -> PairingCode | None:
        result = await self._session.execute(
            select(PairingCode)
            .where(
                PairingCode.user_code_hash == user_code_hash,
                PairingCode.confirmed_at.is_(None),
                PairingCode.consumed_at.is_(None),
                PairingCode.expires_at > now,
            )
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def get_by_device_code(self, device_code_hash: str) -> PairingCode | None:
        result = await self._session.execute(
            select(PairingCode)
            .where(PairingCode.device_code_hash == device_code_hash)
            .with_for_update()
        )
        return result.scalar_one_or_none()
