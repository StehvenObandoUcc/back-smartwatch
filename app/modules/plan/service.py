"""Plan de 7 días de un paciente: generación, versión y ETag."""

import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentDevice, CurrentUser
from app.core.security import utcnow
from app.modules.consents.service import ConsentService
from app.modules.patients.models import Patient
from app.modules.patients.service import PatientService, patient_not_found
from app.modules.plan.generator import PLAN_DAYS, day_start, local_today, plan_doses
from app.modules.plan.repository import PlanRepository
from app.modules.plan.schemas import PlannedDoseOut, PlanOut


def etag_of(plan: PlanOut, tz: ZoneInfo) -> str:
    """Cambia con la versión del plan o con el día local (la ventana de 7 días se desplaza)."""
    return f'"{plan.version}-{local_today(tz, plan.generated_at).isoformat()}"'


def etag_matches(header: str | None, etag: str) -> bool:
    """`If-None-Match`: lista de validadores (con o sin `W/`) o `*`."""
    if not header:
        return False
    tags = {tag.strip().removeprefix("W/") for tag in header.split(",")}
    return "*" in tags or etag in tags


class PlanService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = PlanRepository(session)
        self._patients = PatientService(session)
        self._consents = ConsentService(session)

    async def build(self, patient: Patient, now: datetime | None = None) -> tuple[PlanOut, str]:
        now = now or utcnow()
        tz = ZoneInfo(patient.timezone)
        first = local_today(tz, now)
        last = first + timedelta(days=PLAN_DAYS - 1)
        rules = await self._repo.rules(patient.id, first, last, include_archived=False)
        plan = PlanOut(
            version=patient.plan_version,
            patient_timezone=patient.timezone,
            generated_at=now,
            valid_from=day_start(tz, first),
            valid_until=day_start(tz, first + timedelta(days=PLAN_DAYS)),
            doses=[
                PlannedDoseOut(
                    schedule_id=dose.rule.schedule_id,
                    medication_id=dose.rule.medication_id,
                    medication_name=dose.rule.medication_name,
                    dosage=dose.rule.dosage,
                    instructions=dose.rule.instructions,
                    color=dose.rule.color,
                    scheduled_at=dose.scheduled_at,
                )
                for dose in plan_doses(rules, tz, now)
            ],
        )
        return plan, etag_of(plan, tz)

    async def for_user(self, user: CurrentUser, patient_id: uuid.UUID) -> PlanOut:
        patient = await self._patients.health_access(user, patient_id)
        plan, _ = await self.build(patient)
        return plan

    async def for_device(self, device: CurrentDevice) -> tuple[PlanOut, str]:
        patient = await self._repo.get_patient(device.patient_id)
        if patient is None:
            raise patient_not_found()
        await self._consents.require_health_data(patient.id)
        return await self.build(patient)
