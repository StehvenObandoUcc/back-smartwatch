"""Pacientes, vínculos con cuidadores, invitaciones y consentimientos del paciente.

Reglas de acceso (ver contrato):
- Un paciente sin vínculo activo con el usuario es 404 `patient_not_found`, nunca 403.
- 403 solo para acciones que el rol no permite sobre un paciente accesible.
"""

import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser
from app.core.errors import ConflictError, ForbiddenError, NotFoundError
from app.core.pagination import Page, next_cursor
from app.core.security import hash_token, utcnow
from app.modules.consents.schemas import ConsentListOut, ConsentOut, ConsentUpdate, Purpose
from app.modules.consents.service import Actor, ConsentService, Subject
from app.modules.patients.models import CaregiverInvitation, CareLink, Patient
from app.modules.patients.repository import (
    CareLinkRepository,
    InvitationRepository,
    PatientRepository,
)
from app.modules.patients.schemas import (
    INVITATION_ALPHABET,
    CaregiverInvitationOut,
    CareLinkOut,
    CareLinkPage,
    PatientCreate,
    PatientOut,
    PatientPage,
    PatientUpdate,
)
from app.modules.users.repository import UserRepository

INVITATION_TTL = timedelta(hours=72)
INVITATION_CODE_LENGTH = 10
INVITATION_CODE_PATTERN = re.compile(r"[A-HJKMNP-Z2-9]{10}")


@dataclass(frozen=True, slots=True)
class PatientAccess:
    patient: Patient
    is_owner: bool  # el propio paciente (cuenta con rol patient)

    @property
    def is_caregiver(self) -> bool:
        return not self.is_owner


def to_patient_out(patient: Patient) -> PatientOut:
    return PatientOut(
        id=patient.id,
        display_name=patient.display_name,
        timezone=patient.timezone,
        managed=patient.managed,
        created_at=patient.created_at,
    )


def patient_not_found() -> NotFoundError:
    return NotFoundError("patient_not_found", "El paciente no existe o no tienes acceso.")


class PatientService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._patients = PatientRepository(session)
        self._links = CareLinkRepository(session)
        self._invitations = InvitationRepository(session)
        self._users = UserRepository(session)
        self._consents = ConsentService(session)

    # ─── Acceso ───────────────────────────────────────────────────────────────

    async def access(self, user: CurrentUser, patient_id: uuid.UUID) -> PatientAccess:
        patient = await self._patients.get_accessible(patient_id, user.id)
        if patient is None:
            raise patient_not_found()
        return PatientAccess(patient=patient, is_owner=patient.owner_user_id == user.id)

    async def health_access(self, user: CurrentUser, patient_id: uuid.UUID) -> Patient:
        """Acceso al paciente más su consentimiento `health_data` (medicamentos, plan, tomas)."""
        patient = (await self.access(user, patient_id)).patient
        await self._consents.require_health_data(patient_id)
        return patient

    async def _manageable(self, user: CurrentUser, patient_id: uuid.UUID) -> PatientAccess:
        """Acceso para acciones del titular: el propio paciente o, si es gestionado, un cuidador."""
        access = await self.access(user, patient_id)
        if access.is_caregiver and not access.patient.managed:
            raise ForbiddenError(
                "patient_has_account",
                "Este paciente tiene cuenta propia: solo él puede hacerlo.",
            )
        return access

    # ─── Pacientes ────────────────────────────────────────────────────────────

    async def list(self, user: CurrentUser, page: Page) -> PatientPage:
        rows = await self._patients.list_accessible(user.id, page.cursor, page.limit)
        items, cursor = next_cursor(rows, page.limit, lambda p: (p.created_at, p.id))
        return PatientPage(items=[to_patient_out(p) for p in items], next_cursor=cursor)

    async def create_managed(self, user: CurrentUser, data: PatientCreate) -> PatientOut:
        if user.role != "caregiver":
            raise ForbiddenError(
                "caregivers_only", "Solo un cuidador puede crear pacientes gestionados."
            )
        now = utcnow()
        patient = Patient(
            display_name=data.display_name,
            timezone=data.timezone,
            owner_user_id=None,
            created_by_user_id=user.id,
            created_at=now,
        )
        self._patients.add(patient)
        await self._session.flush()
        self._patients.add(
            CareLink(patient_id=patient.id, caregiver_user_id=user.id, active=True, created_at=now)
        )
        await self._session.commit()
        return to_patient_out(patient)

    async def get(self, user: CurrentUser, patient_id: uuid.UUID) -> PatientOut:
        return to_patient_out((await self.access(user, patient_id)).patient)

    async def update(
        self, user: CurrentUser, patient_id: uuid.UUID, data: PatientUpdate
    ) -> PatientOut:
        patient = (await self._manageable(user, patient_id)).patient
        for field in data.model_fields_set:
            setattr(patient, field, getattr(data, field))
        await self._session.commit()
        await self._session.refresh(patient)
        return to_patient_out(patient)

    # ─── Consentimientos del paciente ─────────────────────────────────────────

    async def list_consents(self, user: CurrentUser, patient_id: uuid.UUID) -> ConsentListOut:
        await self.access(user, patient_id)
        return await self._consents.list(Subject(patient_id=patient_id))

    async def set_consent(
        self, user: CurrentUser, patient_id: uuid.UUID, purpose: Purpose, data: ConsentUpdate
    ) -> ConsentOut:
        access = await self._manageable(user, patient_id)
        actor = await self._users.get(user.id)
        if actor is None:
            raise patient_not_found()
        return await self._consents.set(
            Subject(patient_id=patient_id),
            purpose,
            granted=data.granted,
            version=data.version,
            actor=Actor(
                user_id=actor.id, display_name=actor.display_name, on_behalf=access.is_caregiver
            ),
        )

    # ─── Cuidadores ───────────────────────────────────────────────────────────

    async def list_caregivers(
        self, user: CurrentUser, patient_id: uuid.UUID, page: Page
    ) -> CareLinkPage:
        await self.access(user, patient_id)
        rows = await self._links.list_active(patient_id, page.cursor, page.limit)
        items, cursor = next_cursor(rows, page.limit, lambda r: (r[0].created_at, r[0].id))
        return CareLinkPage(
            items=[_to_link_out(link, name) for link, name in items], next_cursor=cursor
        )

    async def revoke_caregiver(
        self, user: CurrentUser, patient_id: uuid.UUID, caregiver_id: uuid.UUID
    ) -> None:
        access = await self.access(user, patient_id)
        patient = access.patient
        # Quién puede: el paciente con cuenta sobre cualquiera, el cuidador sobre sí mismo y, en
        # un paciente gestionado, cualquier cuidador vinculado sobre otro.
        if access.is_caregiver and caregiver_id != user.id and not patient.managed:
            raise ForbiddenError(
                "patient_has_account", "Solo el paciente puede retirar a otros cuidadores."
            )
        link = await self._links.get(patient_id, caregiver_id)
        if link is None:
            raise NotFoundError("care_link_not_found", "Ese usuario no es cuidador del paciente.")
        if not link.active:
            return  # idempotente
        # Serializa revocaciones simultáneas para que no dejen al paciente sin cuidadores.
        await self._patients.lock(patient_id)
        if patient.managed and await self._links.count_active(patient_id) <= 1:
            raise ConflictError(
                "last_caregiver",
                "Es el último cuidador de un paciente gestionado; quedaría sin nadie a cargo.",
            )
        link.active = False
        link.revoked_at = utcnow()
        link.revoked_by_user_id = user.id
        await self._session.commit()

    # ─── Invitaciones ─────────────────────────────────────────────────────────

    async def create_invitation(
        self, user: CurrentUser, patient_id: uuid.UUID
    ) -> CaregiverInvitationOut:
        access = await self._manageable(user, patient_id)
        now = utcnow()
        code = "".join(secrets.choice(INVITATION_ALPHABET) for _ in range(INVITATION_CODE_LENGTH))
        invitation = CaregiverInvitation(
            patient_id=access.patient.id,
            code_hash=hash_token(code),
            created_by_user_id=user.id,
            expires_at=now + INVITATION_TTL,
            created_at=now,
        )
        self._patients.add(invitation)
        await self._session.commit()
        return CaregiverInvitationOut(
            code=code, patient_id=invitation.patient_id, expires_at=invitation.expires_at
        )

    async def accept_invitation(self, user: CurrentUser, code: str) -> CareLinkOut:
        if user.role != "caregiver":
            raise ForbiddenError("caregivers_only", "Solo un cuidador puede aceptar invitaciones.")
        now = utcnow()
        invitation = (
            await self._invitations.get_usable_for_update(hash_token(code), now)
            if INVITATION_CODE_PATTERN.fullmatch(code)
            else None
        )
        if invitation is None:
            raise NotFoundError(
                "invitation_not_found", "La invitación no existe, caducó o ya se usó."
            )
        link = await self._links.get(invitation.patient_id, user.id)
        if link is not None and link.active:
            raise ConflictError("already_linked", "Ya eres cuidador de este paciente.")
        if link is None:
            link = CareLink(
                patient_id=invitation.patient_id,
                caregiver_user_id=user.id,
                active=True,
                created_at=now,
            )
            self._patients.add(link)
        else:
            link.active, link.revoked_at, link.revoked_by_user_id = True, None, None
        invitation.used_at = now
        invitation.used_by_user_id = user.id
        caregiver = await self._users.get(user.id)
        try:
            await self._session.commit()
        except IntegrityError:
            await self._session.rollback()
            raise ConflictError("already_linked", "Ya eres cuidador de este paciente.") from None
        return _to_link_out(link, caregiver.display_name if caregiver else "")


def _to_link_out(link: CareLink, caregiver_name: str) -> CareLinkOut:
    return CareLinkOut(
        patient_id=link.patient_id,
        caregiver_id=link.caregiver_user_id,
        caregiver_display_name=caregiver_name,
        active=link.active,
        created_at=link.created_at,
    )
