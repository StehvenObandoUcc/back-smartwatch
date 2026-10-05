"""Eventos de toma (reloj y web), historial y adherencia (web).

Todo exige el consentimiento `health_data`. Los usuarios necesitan además vínculo con el paciente
(404 si no); el reloj solo escribe sobre su propio paciente.
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import cast
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentDevice, CurrentUser
from app.core.errors import UnprocessableError
from app.core.pagination import Page, next_cursor
from app.core.security import utcnow
from app.modules.consents.service import ConsentService
from app.modules.doses.history import (
    MAX_RANGE_DAYS,
    HistoryEntry,
    build_history,
    count,
    default_range,
    sort_key,
)
from app.modules.doses.models import DoseEvent
from app.modules.doses.repository import DoseEventRepository
from app.modules.doses.schemas import (
    Adherence,
    DoseEventBatch,
    DoseEventBatchResult,
    DoseEventInput,
    DoseEventResult,
    DoseHistoryItem,
    DoseHistoryPage,
    DoseStatus,
    Outcome,
)
from app.modules.patients.models import Patient
from app.modules.patients.service import PatientService, patient_not_found
from app.modules.plan.generator import ScheduleRule, day_start, is_scheduled, local_today
from app.modules.plan.repository import PlanRepository


def resolve_range(
    from_: date | None, to: date | None, tz: ZoneInfo, now: datetime
) -> tuple[date, date]:
    """Rango en fechas locales del paciente: por defecto los últimos 7 días; máximo 90."""
    last = to or local_today(tz, now)
    first = from_ or default_range(last)[0]
    if first > last:
        raise UnprocessableError(["query", "from"], "from no puede ser posterior a to")
    if (last - first).days + 1 > MAX_RANGE_DAYS:
        raise UnprocessableError(["query", "from"], f"El rango máximo es de {MAX_RANGE_DAYS} días")
    return first, last


def _to_item(entry: HistoryEntry) -> DoseHistoryItem:
    return DoseHistoryItem(
        schedule_id=entry.schedule_id,
        medication_id=entry.medication_id,
        medication_name=entry.medication_name,
        dosage=entry.dosage,
        scheduled_at=entry.scheduled_at,
        status=cast(DoseStatus, entry.status),
        acted_at=entry.acted_at,
    )


class DoseService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._events = DoseEventRepository(session)
        self._plan = PlanRepository(session)
        self._patients = PatientService(session)
        self._consents = ConsentService(session)

    # ─── Subir eventos (reloj o web) ──────────────────────────────────────────

    async def record(self, device: CurrentDevice, batch: DoseEventBatch) -> DoseEventBatchResult:
        patient = await self._plan.get_patient(device.patient_id)
        if patient is None:
            raise patient_not_found()
        await self._consents.require_health_data(patient.id)
        return await self._store(patient, device.id, batch)

    async def record_for_patient(
        self, user: CurrentUser, patient_id: uuid.UUID, batch: DoseEventBatch
    ) -> DoseEventBatchResult:
        """Tomas marcadas desde la web: el propio paciente o un cuidador vinculado; sin reloj."""
        patient = await self._patients.health_access(user, patient_id)
        return await self._store(patient, None, batch)

    async def _store(
        self, patient: Patient, device_id: uuid.UUID | None, batch: DoseEventBatch
    ) -> DoseEventBatchResult:
        tz = ZoneInfo(patient.timezone)
        rules = {
            rule.schedule_id: rule
            for rule in await self._plan.rules_for_schedules(
                patient.id, {event.schedule_id for event in batch.events}
            )
        }
        # ponytail: un INSERT por evento (máx. 100); uno múltiple si el volumen lo pide
        results = [
            await self._record_one(device_id, patient.id, tz, rules, e) for e in batch.events
        ]
        await self._session.commit()
        return DoseEventBatchResult(results=results)

    async def _record_one(
        self,
        device_id: uuid.UUID | None,
        patient_id: uuid.UUID,
        tz: ZoneInfo,
        rules: dict[uuid.UUID, ScheduleRule],
        event: DoseEventInput,
    ) -> DoseEventResult:
        def result(outcome: Outcome, code: str | None = None) -> DoseEventResult:
            return DoseEventResult(event_id=event.event_id, outcome=outcome, code=code)

        # Un reintento se reconoce antes de validar: el horario pudo cambiar desde el primer envío.
        if await self._events.event_exists(event.event_id):
            return result("duplicate")
        rule = rules.get(event.schedule_id)
        if rule is None:
            return result("rejected", "schedule_not_found")
        if not is_scheduled(rule, tz, event.scheduled_at):
            return result("rejected", "invalid_scheduled_at")
        created = await self._events.insert_if_new(
            DoseEvent(
                event_id=event.event_id,
                patient_id=patient_id,
                device_id=device_id,
                schedule_id=event.schedule_id,
                medication_id=rule.medication_id,
                scheduled_at=event.scheduled_at,
                status=event.status,
                acted_at=event.acted_at,
            )
        )
        if created:
            return result("created")
        # Sin inserción: ganó otro envío del mismo evento (carrera) o la dosis ya tenía otro.
        if await self._events.event_exists(event.event_id):
            return result("duplicate")
        return result("rejected", "dose_already_recorded")

    # ─── Web: historial y adherencia ──────────────────────────────────────────

    async def _entries(
        self, user: CurrentUser, patient_id: uuid.UUID, from_: date | None, to: date | None
    ) -> tuple[Patient, date, date, list[HistoryEntry]]:
        patient = await self._patients.health_access(user, patient_id)
        tz = ZoneInfo(patient.timezone)
        now = utcnow()
        first, last = resolve_range(from_, to, tz, now)
        events = await self._events.in_range(
            patient.id, day_start(tz, first), day_start(tz, last + timedelta(days=1))
        )
        rules = await self._plan.rules(patient.id, first, last, include_archived=True)
        return patient, first, last, build_history(rules, events, tz, first, last, now)

    async def history(
        self,
        user: CurrentUser,
        patient_id: uuid.UUID,
        from_: date | None,
        to: date | None,
        page: Page,
    ) -> DoseHistoryPage:
        _, _, _, entries = await self._entries(user, patient_id, from_, to)
        if page.cursor is not None:
            # Orden descendente: la página siguiente son las entradas estrictamente anteriores.
            at, schedule_id = page.cursor
            cursor_key = (at.astimezone(UTC), schedule_id)
            entries = [e for e in entries if sort_key(e) < cursor_key]
        rows = entries[: page.limit + 1]
        items, cursor = next_cursor(rows, page.limit, lambda e: (e.scheduled_at, e.schedule_id))
        return DoseHistoryPage(items=[_to_item(e) for e in items], next_cursor=cursor)

    async def adherence(
        self, user: CurrentUser, patient_id: uuid.UUID, from_: date | None, to: date | None
    ) -> Adherence:
        _, first, last, entries = await self._entries(user, patient_id, from_, to)
        counts = count(entries)
        return Adherence.model_validate(
            {
                "from": first,
                "to": last,
                "taken": counts.taken,
                "skipped": counts.skipped,
                "missed": counts.missed,
                "percentage": counts.percentage,
            }
        )
