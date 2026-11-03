import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ForbiddenError, UnauthorizedError
from app.core.logging import get_logger
from app.core.security import PasswordService
from app.modules.chat.repository import ChatUsageRepository
from app.modules.consents.schemas import ConsentListOut, ConsentOut, ConsentUpdate, Purpose
from app.modules.consents.service import Actor, ConsentService, Subject
from app.modules.devices.repository import DeviceRepository
from app.modules.notifications.repository import OutboxRepository, TelegramLinkRepository
from app.modules.patients.repository import PatientRepository
from app.modules.users.models import User
from app.modules.users.repository import UserRepository
from app.modules.users.schemas import UserOut, UserUpdate

logger = get_logger(__name__)


def to_user_out(user: User, patient_id: uuid.UUID | None) -> UserOut:
    return UserOut(
        id=user.id,
        email=user.email,
        email_verified=user.email_verified_at is not None,
        display_name=user.display_name,
        role=user.role,
        timezone=user.timezone,
        locale=user.locale,
        patient_id=patient_id,
        created_at=user.created_at,
    )


class UserService:
    def __init__(self, session: AsyncSession, passwords: PasswordService) -> None:
        self._session = session
        self._passwords = passwords
        self._users = UserRepository(session)
        self._patients = PatientRepository(session)
        self._consents = ConsentService(session)

    async def _get(self, user_id: uuid.UUID) -> User:
        user = await self._users.get(user_id)
        if user is None:
            # Token válido de un usuario que ya no existe.
            raise UnauthorizedError("invalid_token", "La sesión ya no es válida.")
        return user

    async def _out(self, user: User) -> UserOut:
        return to_user_out(user, patient_id=await self._patients.get_owned_id(user.id))

    async def _subject(self, user: User) -> Subject:
        """Un paciente con cuenta decide sobre su registro de paciente; un cuidador, sobre sí."""
        patient_id = await self._patients.get_owned_id(user.id)
        return Subject(patient_id=patient_id) if patient_id else Subject(user_id=user.id)

    async def get_me(self, user_id: uuid.UUID) -> UserOut:
        return await self._out(await self._get(user_id))

    async def update_me(self, user_id: uuid.UUID, data: UserUpdate) -> UserOut:
        user = await self._get(user_id)
        for field in data.model_fields_set:
            setattr(user, field, getattr(data, field))
        await self._session.commit()
        await self._session.refresh(user)
        return await self._out(user)

    async def list_my_consents(self, user_id: uuid.UUID) -> ConsentListOut:
        user = await self._get(user_id)
        return await self._consents.list(await self._subject(user))

    async def set_my_consent(
        self, user_id: uuid.UUID, purpose: Purpose, data: ConsentUpdate
    ) -> ConsentOut:
        user = await self._get(user_id)
        return await self._consents.set(
            await self._subject(user),
            purpose,
            granted=data.granted,
            version=data.version,
            actor=Actor(user_id=user.id, display_name=user.display_name, on_behalf=False),
        )

    async def delete_me(self, user_id: uuid.UUID, password: str) -> None:
        """Borra la cuenta y los datos de salud que dependen solo de ella, en una transacción.

        - El registro de paciente propio (si lo hay) se borra con todo su contenido.
        - Un paciente gestionado se borra solo si este usuario es su único cuidador activo; si hay
          más cuidadores se conserva y solo desaparece el vínculo de este usuario.
        - Se borran el uso del chat (del usuario y de los relojes de los pacientes borrados) y los
          mensajes sin enviar dirigidos a su correo o a su chat de Telegram.
        - El historial de consentimientos que otros conservan queda sin autor (FK `SET NULL`).
        """
        user = await self._get(user_id)
        if not await self._passwords.verify(user.password_hash, password):
            raise ForbiddenError("invalid_password", "La contraseña no es correcta.")

        owned_id = await self._patients.get_owned_id(user.id)
        sole_managed, shared_managed = await self._patients.lock_managed_split(user.id)
        patient_ids = ([owned_id] if owned_id else []) + sole_managed
        device_ids = await DeviceRepository(self._session).ids_for_patients(patient_ids)

        await ChatUsageRepository(self._session).delete_for_principals([user.id, *device_ids])
        link = await TelegramLinkRepository(self._session).get_for_user(user.id)
        await OutboxRepository(self._session).delete_unsent_for(
            email=user.email, chat_id=link.chat_id if link else None
        )
        await self._patients.delete_many(patient_ids)
        role = user.role
        await self._users.delete(user)
        await self._session.commit()

        logger.info(
            "account_deleted",
            user_id=str(user_id),
            role=role,
            patients_deleted=len(patient_ids),
            patients_kept=len(shared_managed),
        )
