"""Quién recibe un aviso y por qué canal.

Un aviso sale por un canal solo si: el canal está vinculado (correo verificado / chat de Telegram),
la preferencia del usuario lo permite y el usuario concedió el consentimiento `notifications`
(el de su registro de paciente, si es un paciente con cuenta; si no, el suyo propio).
"""

import uuid
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.consents.repository import ConsentRepository
from app.modules.notifications.models import NotificationPreference, TelegramLink
from app.modules.patients.models import CareLink, Patient
from app.modules.users.models import User

Event = Literal["missed_dose", "weekly_report"]


@dataclass(frozen=True, slots=True)
class Recipient:
    user_id: uuid.UUID
    email: str
    email_verified: bool
    chat_id: int | None
    prefs: dict[str, bool]  # claves como "missed_dose_email"
    consent: bool

    def channels(self, event: Event) -> list[str]:
        """Canales por los que este usuario debe recibir `event` (puede ser ninguno)."""
        if not self.consent:
            return []
        channels = []
        if self.email_verified and self.prefs[f"{event}_email"]:
            channels.append("email")
        if self.chat_id is not None and self.prefs[f"{event}_telegram"]:
            channels.append("telegram")
        return channels


_PREF_FIELDS = (
    "missed_dose_email",
    "missed_dose_telegram",
    "weekly_report_email",
    "weekly_report_telegram",
)


async def resolve_recipients(
    session: AsyncSession, patient: Patient, *, include_owner: bool
) -> list[Recipient]:
    """Cuidadores con vínculo activo y, si `include_owner`, el paciente con cuenta propia."""
    caregivers = select(CareLink.caregiver_user_id).where(
        CareLink.patient_id == patient.id, CareLink.active.is_(True)
    )
    user_ids = set((await session.execute(caregivers)).scalars())
    if include_owner and patient.owner_user_id is not None:
        user_ids.add(patient.owner_user_id)
    if not user_ids:
        return []

    rows = await session.execute(
        select(User, TelegramLink.chat_id, NotificationPreference)
        .outerjoin(TelegramLink, TelegramLink.user_id == User.id)
        .outerjoin(NotificationPreference, NotificationPreference.user_id == User.id)
        .where(User.id.in_(user_ids))
        .order_by(User.created_at, User.id)
    )
    consents = ConsentRepository(session)
    recipients = []
    for user, chat_id, pref in rows.tuples():
        is_owner = user.id == patient.owner_user_id
        consent = (
            await consents.is_granted(patient.id, "notifications")
            if is_owner
            else await consents.is_granted_for_user(user.id, "notifications")
        )
        recipients.append(
            Recipient(
                user_id=user.id,
                email=user.email,
                email_verified=user.email_verified_at is not None,
                chat_id=chat_id,
                # Sin fila de preferencias, todo activado.
                prefs={f: getattr(pref, f) if pref else True for f in _PREF_FIELDS},
                consent=consent,
            )
        )
    return recipients
