import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import UnauthorizedError
from app.modules.consents.schemas import ConsentListOut, ConsentOut, ConsentUpdate, Purpose
from app.modules.consents.service import ConsentService
from app.modules.users.models import User
from app.modules.users.repository import UserRepository
from app.modules.users.schemas import UserOut, UserUpdate


def to_user_out(user: User, patient_id: uuid.UUID | None) -> UserOut:
    return UserOut(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        timezone=user.timezone,
        locale=user.locale,
        patient_id=patient_id,
        created_at=user.created_at,
    )


class UserService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._users = UserRepository(session)
        self._consents = ConsentService(session)

    async def _get(self, user_id: uuid.UUID) -> User:
        user = await self._users.get(user_id)
        if user is None:
            # Token válido de un usuario que ya no existe.
            raise UnauthorizedError("invalid_token", "La sesión ya no es válida.")
        return user

    async def get_me(self, user_id: uuid.UUID) -> UserOut:
        return to_user_out(await self._get(user_id), patient_id=None)

    async def update_me(self, user_id: uuid.UUID, data: UserUpdate) -> UserOut:
        user = await self._get(user_id)
        for field in data.model_fields_set:
            setattr(user, field, getattr(data, field))
        await self._session.commit()
        await self._session.refresh(user)
        return to_user_out(user, patient_id=None)

    async def list_my_consents(self, user_id: uuid.UUID) -> ConsentListOut:
        await self._get(user_id)
        return await self._consents.list_for_user(user_id)

    async def set_my_consent(
        self, user_id: uuid.UUID, purpose: Purpose, data: ConsentUpdate
    ) -> ConsentOut:
        user = await self._get(user_id)
        return await self._consents.set_for_user(
            user.id,
            purpose,
            granted=data.granted,
            version=data.version,
            actor_name=user.display_name,
        )
