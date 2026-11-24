"""Chat con IA sobre el plan del paciente (web por SSE, reloj con respuesta corta).

Orden de las comprobaciones: sesión → acceso al paciente (404) → consentimientos `health_data` y
`ai_chat` (403) → límite por minuto (429) → cupo diario (429) → proveedor (503). Un fallo del
proveedor devuelve el mensaje reservado. Del texto de la conversación no se guarda ni se registra
nada: solo contadores de mensajes y tokens.

En el reloj el modelo puede proponer un medicamento nuevo (`create_medication`) o un horario más
para uno que ya existe (`add_schedule`): la propuesta se guarda en Redis 10 minutos y solo se crea
cuando el reloj la confirma con `confirmActionId`.
"""

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import ValidationError
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.auth import CurrentDevice, CurrentUser
from app.core.config import Settings
from app.core.errors import DomainError, NotFoundError
from app.core.logging import get_logger
from app.core.rate_limit import RateLimiter
from app.core.security import utcnow
from app.modules.chat.prompt import (
    MAX_MEDICATIONS_IN_CONTEXT,
    WATCH_TOOLS,
    Lang,
    PromptAdherence,
    PromptCounts,
    PromptDose,
    PromptMedication,
    build_system_prompt,
    describe_medication,
    describe_schedule,
    shorten_for_watch,
)
from app.modules.chat.provider import (
    ChatProvider,
    ProviderError,
    StreamEvent,
    ToolCall,
    Turn,
    Usage,
)
from app.modules.chat.repository import ChatUsageRepository
from app.modules.chat.schemas import (
    ArchiveDraft,
    ChangeProposal,
    ChatAction,
    ChatRequest,
    DosageChangeDraft,
    MedicationDraft,
    PendingAction,
    ScheduleDraft,
    ScheduleProposal,
    TimesChangeDraft,
    WatchChatReply,
    WatchChatRequest,
)
from app.modules.consents.service import ConsentService
from app.modules.doses.history import HistoryEntry, count
from app.modules.doses.loader import load_history
from app.modules.medications.repository import MedicationRepository
from app.modules.medications.schemas import DeviceMedicationCreate, ScheduleCreate
from app.modules.medications.service import MedicationService
from app.modules.patients.models import Patient
from app.modules.patients.service import PatientService, patient_not_found
from app.modules.plan.generator import day_start, doses_in_range, local_today
from app.modules.plan.repository import PlanRepository
from app.modules.users.repository import UserRepository

logger = get_logger(__name__)

_STATUS_LABELS = {"TAKEN": "tomada", "SKIPPED": "omitida", "MISSED": "sin registrar"}
PENDING_ACTION_TTL_SECONDS = 600
ADHERENCE_DAYS = 30  # el prompt lleva la adherencia del mes y de los últimos 30 días

# Lo que escribe el servidor (no el modelo) en el reloj, en el idioma de la cuenta.
_TEXTS: dict[Lang, dict[str, str]] = {
    "es": {
        "propose_medication": "Voy a agregar {}. ¿Lo confirmas?",
        "add": "Agregar {}",
        "confirm": "{}. ¿Lo confirmas?",
        "not_understood": "No entendí bien el medicamento o la hora. ¿Me lo repites?",
        "done": "Listo, agregué {}.",
        "update_schedule": "Cambiar {} a las {}",
        "update_dosage": "Cambiar la dosis de {} de {} a {}",
        "archive_medication": "Dejar de recordar {} {}",
        "schedule_updated": "Listo, {} ahora suena a las {}.",
        "dosage_updated": "Listo, la dosis de {} ahora es {}.",
        "medication_archived": "Listo, ya no recordaré {}. Su historial se conserva.",
    },
    "en": {
        "propose_medication": "I'll add {}. Do you confirm?",
        "add": "Add {}",
        "confirm": "{}. Do you confirm?",
        "not_understood": "I didn't catch the medication or the time. Could you say it again?",
        "done": "Done, I added {}.",
        "update_schedule": "Change {} to {}",
        "update_dosage": "Change the dose of {} from {} to {}",
        "archive_medication": "Stop reminding {} {}",
        "schedule_updated": "Done, {} now rings at {}.",
        "dosage_updated": "Done, the dose of {} is now {}.",
        "medication_archived": "Done, I will not remind you of {} anymore. Its history is kept.",
    },
}


class ChatUnavailableError(DomainError):
    status_code = 503
    default_title = "Chat no disponible"

    def __init__(self) -> None:
        super().__init__("chat_unavailable", "El asistente no responde ahora. Inténtalo de nuevo.")


class ChatLimitReachedError(DomainError):
    status_code = 429
    default_title = "Límite diario del chat"

    def __init__(self, retry_after: int) -> None:
        super().__init__(
            "chat_limit_reached",
            "Alcanzaste el límite de mensajes de hoy. Vuelve a intentarlo mañana.",
            headers={"Retry-After": str(max(retry_after, 1))},
        )


def sse(event: str, data: dict[str, Any]) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode()


class ChatService:
    def __init__(
        self,
        session: AsyncSession,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        provider: ChatProvider,
        limiter: RateLimiter,
        redis: Redis,
    ) -> None:
        self._session = session
        self._factory = session_factory
        self._settings = settings
        self._provider = provider
        self._limiter = limiter
        self._redis = redis
        self._patients = PatientService(session)
        self._consents = ConsentService(session)
        self._plan = PlanRepository(session)
        self._medications = MedicationRepository(session)
        self._usage = ChatUsageRepository(session)

    # ─── Pasos comunes ────────────────────────────────────────────────────────

    async def _require_consents(self, patient_id: uuid.UUID) -> None:
        await self._consents.require(patient_id, "health_data")
        await self._consents.require(patient_id, "ai_chat")

    async def _prompt(self, patient: Patient, now: datetime, *, short: bool) -> str:
        tz = ZoneInfo(patient.timezone)
        today = local_today(tz, now)
        yesterday = today - timedelta(days=1)
        rules = await self._plan.rules(
            patient.id, yesterday, today + timedelta(days=1), include_archived=False
        )
        first = min(today - timedelta(days=ADHERENCE_DAYS - 1), today.replace(day=1))
        history = await load_history(self._session, patient, first, today, now)
        done = {
            (e.schedule_id, e.scheduled_at.astimezone(UTC)): _STATUS_LABELS[e.status]
            for e in history
            if e.scheduled_at.date() >= yesterday
        }
        doses = sorted(
            (
                PromptDose(
                    at=dose.scheduled_at,
                    medication=rule.medication_name,
                    dosage=rule.dosage,
                    instructions=rule.instructions,
                    status=done.get(
                        (rule.schedule_id, dose.scheduled_at.astimezone(UTC)), "pendiente"
                    ),
                )
                for rule in rules
                for dose in doses_in_range(rule, tz, yesterday, today + timedelta(days=1))
                # Lo de ayer sin evento ni MISSED (antes de `effective_from`) no se muestra.
                if dose.scheduled_at.date() >= today
                or (rule.schedule_id, dose.scheduled_at.astimezone(UTC)) in done
            ),
            key=lambda d: d.at.astimezone(UTC),
        )
        medications = [
            PromptMedication(
                medication.id,
                medication.name,
                medication.dosage,
                [(s.times, s.days_of_week) for s in schedules],
            )
            for medication, schedules in await self._medications.active_with_schedules(
                patient.id, today, MAX_MEDICATIONS_IN_CONTEXT
            )
        ]
        return build_system_prompt(
            now.astimezone(tz),
            doses,
            short=short,
            medications=medications,
            adherence=_adherence(history, today),
        )

    async def _lang(self, patient: Patient) -> Lang:
        """Idioma de la cuenta del paciente o, si es gestionado, de quien lo creó."""
        user_id = patient.owner_user_id or patient.created_by_user_id
        user = await UserRepository(self._session).get(user_id) if user_id else None
        return "en" if user is not None and user.locale == "en" else "es"

    async def _reserve(
        self, principal_id: uuid.UUID, patient: Patient, now: datetime
    ) -> tuple[date, int]:
        """Reserva un mensaje del cupo diario (día local del paciente). Devuelve los que quedan."""
        tz = ZoneInfo(patient.timezone)
        day = local_today(tz, now)
        limit = self._settings.chat_daily_messages
        used = await self._usage.reserve(principal_id, day, limit)
        if used is None:
            await self._session.rollback()
            tomorrow = day_start(tz, day + timedelta(days=1))
            raise ChatLimitReachedError(int((tomorrow - now).total_seconds()))
        await self._session.commit()
        return day, limit - used

    async def _release(self, principal_id: uuid.UUID, day: date) -> None:
        await self._usage.release(principal_id, day)
        await self._session.commit()

    async def _record_tokens(
        self, principal_id: uuid.UUID, day: date, usage: Usage | None, system: str, reply: str
    ) -> None:
        # Sin cifras del proveedor se estima (~4 caracteres por token).
        used = usage or Usage(len(system) // 4, len(reply) // 4)
        async with self._factory() as session:
            await ChatUsageRepository(session).add_tokens(
                principal_id, day, used.input_tokens, used.output_tokens
            )
            await session.commit()
        logger.info(
            "chat_tokens",
            principal_id=str(principal_id),
            input_tokens=used.input_tokens,
            output_tokens=used.output_tokens,
        )

    @staticmethod
    def _turns(data: ChatRequest) -> list[Turn]:
        return [Turn(t.role, t.content) for t in data.history] + [Turn("user", data.message)]

    # ─── Web: SSE ─────────────────────────────────────────────────────────────

    async def start_web(
        self, user: CurrentUser, patient_id: uuid.UUID, data: ChatRequest
    ) -> AsyncIterator[bytes]:
        patient = await self._patients.health_access(user, patient_id)
        await self._consents.require(patient.id, "ai_chat")
        await self._limiter.hit("chat:principal", str(user.id), self._settings.rate_chat_user)
        now = utcnow()
        system = await self._prompt(patient, now, short=False)
        day, remaining = await self._reserve(user.id, patient, now)
        events = self._provider.stream(
            system, self._turns(data), max_tokens=self._settings.chat_web_max_tokens
        )
        # Se lee el primer trozo antes de abrir el flujo: si el proveedor falla al conectar, es un
        # 503 normal (y el mensaje no se gasta) en vez de un flujo que muere a medias.
        first: StreamEvent | None = None
        try:
            first = await anext(events, None)
        except ProviderError as exc:
            logger.warning("chat_provider_failed", reason=exc.reason)
            await self._release(user.id, day)
            raise ChatUnavailableError from None
        return self._sse(events, first, user.id, day, remaining, system)

    async def _sse(
        self,
        events: AsyncIterator[StreamEvent],
        first: StreamEvent | None,
        principal_id: uuid.UUID,
        day: date,
        remaining: int,
        system: str,
    ) -> AsyncIterator[bytes]:
        usage: Usage | None = None
        reply: list[str] = []
        try:
            async for event in _prepend(first, events):
                if event.text:
                    reply.append(event.text)
                    yield sse("delta", {"text": event.text})
                if event.usage:
                    usage = event.usage
            yield sse("done", {"remainingMessages": remaining})
        except ProviderError as exc:
            logger.warning("chat_provider_failed", reason=exc.reason)
            yield sse("error", {"code": "chat_unavailable"})
        finally:
            # shield: si el cliente se desconecta, el recuento de tokens se guarda igual.
            await asyncio.shield(
                self._record_tokens(principal_id, day, usage, system, "".join(reply))
            )

    # ─── Reloj: respuesta corta ───────────────────────────────────────────────

    async def reply_watch(self, device: CurrentDevice, data: WatchChatRequest) -> WatchChatReply:
        patient = await self._plan.get_patient(device.patient_id)
        if patient is None:
            raise patient_not_found()
        await self._require_consents(patient.id)
        await self._limiter.hit("chat:principal", str(device.id), self._settings.rate_chat_user)
        now = utcnow()
        lang = await self._lang(patient)
        text_for = _TEXTS[lang]
        if data.confirm_action_id is not None:
            return await self._confirm(device.id, patient, data.confirm_action_id, now, lang)
        turns = [Turn(t.role, t.content) for t in data.history] + [Turn("user", data.message or "")]
        system = await self._prompt(patient, now, short=True)
        day, remaining = await self._reserve(device.id, patient, now)
        text: list[str] = []
        calls: list[ToolCall] = []
        usage: Usage | None = None
        try:
            async for event in self._provider.stream(
                system,
                turns,
                max_tokens=self._settings.chat_watch_max_tokens,
                tools=WATCH_TOOLS,
            ):
                if event.text:
                    text.append(event.text)
                if event.tool_call:
                    calls.append(event.tool_call)
                if event.usage:
                    usage = event.usage
        except ProviderError as exc:
            logger.warning("chat_provider_failed", reason=exc.reason)
            await self._release(device.id, day)
            raise ChatUnavailableError from None
        pending: PendingAction | None = None
        for draft in _drafts(calls):
            if isinstance(draft, MedicationDraft):
                pending = await self._propose(device.id, patient, draft, now, lang)
                reply = text_for["propose_medication"].format(pending.summary)
                break
            if isinstance(draft, ScheduleDraft):
                pending = await self._propose_schedule(device.id, patient, draft, now, lang)
            else:
                pending = await self._propose_change(device.id, patient, draft, lang)
            if pending is not None:
                reply = text_for["confirm"].format(pending.summary)
                break
        if pending is None and calls:  # pidió agregar algo, pero los datos no son válidos
            reply = text_for["not_understood"]
        elif pending is None:
            reply = shorten_for_watch("".join(text))
        if not reply:
            await self._release(device.id, day)
            raise ChatUnavailableError
        await self._record_tokens(device.id, day, usage, system, "".join(text))
        return WatchChatReply(reply=reply, remaining_messages=remaining, pending_action=pending)

    async def _propose(
        self,
        device_id: uuid.UUID,
        patient: Patient,
        draft: MedicationDraft,
        now: datetime,
        lang: Lang,
    ) -> PendingAction:
        """Guarda la propuesta sin crear nada. Su id es el `clientId` del medicamento: así
        reintentar la confirmación devuelve el mismo en vez de duplicarlo."""
        action_id = uuid.uuid4()
        create = DeviceMedicationCreate(
            client_id=action_id,
            name=draft.name,
            dosage=draft.dosage,
            schedule=ScheduleCreate(
                times=draft.times,
                days_of_week=draft.days_of_week,
                start_date=local_today(ZoneInfo(patient.timezone), now),
            ),
        )
        await self._redis.set(
            _action_key(device_id, action_id),
            create.model_dump_json(by_alias=True),
            ex=PENDING_ACTION_TTL_SECONDS,
        )
        return PendingAction(id=action_id, summary=_summary(create, lang))

    async def _confirm(
        self,
        device_id: uuid.UUID,
        patient: Patient,
        action_id: uuid.UUID,
        now: datetime,
        lang: Lang,
    ) -> WatchChatReply:
        """Crea lo propuesto. No llama al modelo ni gasta el cupo diario."""
        raw = await self._redis.get(_action_key(device_id, action_id))
        if raw is None:
            raise NotFoundError(
                "chat_action_not_found", "La propuesta caducó o no existe. Pídela de nuevo."
            )
        used = await self._usage.messages(device_id, local_today(ZoneInfo(patient.timezone), now))
        remaining = max(self._settings.chat_daily_messages - used, 0)
        stored = json.loads(raw)
        if stored.get("type") in _CHANGE_TYPES:
            return await self._confirm_change(
                device_id,
                patient,
                action_id,
                ChangeProposal.model_validate(stored),
                remaining,
                now,
                lang,
            )
        if stored.get("type") == "add_schedule":
            return await self._confirm_schedule(
                device_id,
                patient,
                action_id,
                ScheduleProposal.model_validate(stored),
                remaining,
                lang,
            )
        create = DeviceMedicationCreate.model_validate(stored)
        created, _ = await MedicationService(self._session).create_from_device(patient.id, create)
        return WatchChatReply(
            reply=_TEXTS[lang]["done"].format(_summary(create, lang)),
            remaining_messages=remaining,
            actions=[
                ChatAction(medication_id=created.medication.id, schedule_id=created.schedule.id)
            ],
        )

    async def _propose_schedule(
        self,
        device_id: uuid.UUID,
        patient: Patient,
        draft: ScheduleDraft,
        now: datetime,
        lang: Lang,
    ) -> PendingAction | None:
        """Como `_propose`, para un medicamento que ya existe. Si no es del paciente o está
        archivado, la llamada se ignora (None)."""
        medication = await self._medications.get(patient.id, draft.medication_id)
        if medication is None or medication.archived_at is not None:
            return None
        action_id = uuid.uuid4()
        proposal = ScheduleProposal(
            medication_id=medication.id,
            schedule=ScheduleCreate(
                times=draft.times,
                days_of_week=draft.days_of_week,
                start_date=local_today(ZoneInfo(patient.timezone), now),
            ),
            description=describe_schedule(
                medication.name, medication.dosage, draft.times, draft.days_of_week, lang
            ),
        )
        await self._save(device_id, action_id, proposal, PENDING_ACTION_TTL_SECONDS)
        return PendingAction(
            id=action_id,
            type="add_schedule",
            summary=_TEXTS[lang]["add"].format(proposal.description),
        )

    async def _confirm_schedule(
        self,
        device_id: uuid.UUID,
        patient: Patient,
        action_id: uuid.UUID,
        proposal: ScheduleProposal,
        remaining: int,
        lang: Lang,
    ) -> WatchChatReply:
        schedule_id = proposal.schedule_id
        if schedule_id is None:
            # Límite: dos confirmaciones simultáneas de la misma propuesta crearían dos
            # horarios; el reloj confirma una vez y reintenta en serie. Con un SET NX de
            # «en curso» se cerraría si hiciera falta.
            schedule = await MedicationService(self._session).add_schedule_from_device(
                patient.id, proposal.medication_id, proposal.schedule
            )
            schedule_id = schedule.id
            # Lo confirmado se queda el resto de los 10 minutos: reintentar no duplica.
            ttl = await self._redis.ttl(_action_key(device_id, action_id))
            done = proposal.model_copy(update={"schedule_id": schedule_id})
            await self._save(device_id, action_id, done, max(ttl, 1))
        return WatchChatReply(
            reply=_TEXTS[lang]["done"].format(proposal.description),
            remaining_messages=remaining,
            actions=[
                ChatAction(
                    type="schedule_added",
                    medication_id=proposal.medication_id,
                    schedule_id=schedule_id,
                )
            ],
        )

    async def _propose_change(
        self,
        device_id: uuid.UUID,
        patient: Patient,
        draft: TimesChangeDraft | DosageChangeDraft | ArchiveDraft,
        lang: Lang,
    ) -> PendingAction | None:
        """Cambio sobre un medicamento que ya existe: se guarda sin aplicar hasta que el reloj lo
        confirme. Si no es del paciente o está archivado, la llamada se ignora (None)."""
        medication = await self._medications.get(patient.id, draft.medication_id)
        if medication is None or medication.archived_at is not None:
            return None
        text_for = _TEXTS[lang]
        if isinstance(draft, TimesChangeDraft):
            proposal = ChangeProposal(
                type="update_schedule",
                medication_id=medication.id,
                times=draft.times,
                days_of_week=draft.days_of_week,
                description=text_for["update_schedule"].format(
                    f"{medication.name} {medication.dosage}",
                    _when_times(draft.times, draft.days_of_week, lang),
                ),
            )
        elif isinstance(draft, DosageChangeDraft):
            proposal = ChangeProposal(
                type="update_dosage",
                medication_id=medication.id,
                dosage=draft.dosage,
                description=text_for["update_dosage"].format(
                    medication.name, medication.dosage, draft.dosage
                ),
            )
        else:
            proposal = ChangeProposal(
                type="archive_medication",
                medication_id=medication.id,
                description=text_for["archive_medication"].format(
                    medication.name, medication.dosage
                ),
            )
        action_id = uuid.uuid4()
        await self._redis.set(
            _action_key(device_id, action_id),
            proposal.model_dump_json(by_alias=True),
            ex=PENDING_ACTION_TTL_SECONDS,
        )
        return PendingAction(id=action_id, type=proposal.type, summary=proposal.description)

    async def _confirm_change(
        self,
        device_id: uuid.UUID,
        patient: Patient,
        action_id: uuid.UUID,
        proposal: ChangeProposal,
        remaining: int,
        now: datetime,
        lang: Lang,
    ) -> WatchChatReply:
        """Aplica el cambio una sola vez: reintentar la confirmación devuelve el mismo resultado."""
        medication = await self._medications.get(patient.id, proposal.medication_id)
        if medication is None:
            raise NotFoundError(
                "chat_action_not_found", "La propuesta caducó o no existe. Pídela de nuevo."
            )
        name = medication.name
        schedule_id = proposal.schedule_id
        if not proposal.done:
            service = MedicationService(self._session)
            if proposal.type == "update_schedule":
                schedule = await service.replace_times_from_device(
                    patient.id,
                    proposal.medication_id,
                    proposal.times or [],
                    proposal.days_of_week or [1, 2, 3, 4, 5, 6, 7],
                    local_today(ZoneInfo(patient.timezone), now),
                )
                schedule_id = schedule.id
            elif proposal.type == "update_dosage":
                await service.update_dosage_from_device(
                    patient.id, proposal.medication_id, proposal.dosage or ""
                )
            else:
                await service.archive_from_device(patient.id, proposal.medication_id)
            ttl = await self._redis.ttl(_action_key(device_id, action_id))
            done = proposal.model_copy(update={"done": True, "schedule_id": schedule_id})
            await self._redis.set(
                _action_key(device_id, action_id),
                done.model_dump_json(by_alias=True),
                ex=max(ttl, 1),
            )
        text_for = _TEXTS[lang]
        if proposal.type == "update_schedule":
            action_type: ActionType = "schedule_updated"
            reply = text_for["schedule_updated"].format(
                name, _when_times(proposal.times or [], proposal.days_of_week or [], lang)
            )
        elif proposal.type == "update_dosage":
            action_type = "dosage_updated"
            reply = text_for["dosage_updated"].format(name, proposal.dosage)
        else:
            action_type = "medication_archived"
            reply = text_for["medication_archived"].format(name)
        return WatchChatReply(
            reply=reply,
            remaining_messages=remaining,
            actions=[
                ChatAction(
                    type=action_type, medication_id=proposal.medication_id, schedule_id=schedule_id
                )
            ],
        )

    async def _save(
        self, device_id: uuid.UUID, action_id: uuid.UUID, proposal: ScheduleProposal, ttl: int
    ) -> None:
        await self._redis.set(
            _action_key(device_id, action_id), proposal.model_dump_json(by_alias=True), ex=ttl
        )


def _counts(entries: list[HistoryEntry]) -> PromptCounts:
    c = count(entries)
    return PromptCounts(c.taken, c.skipped, c.missed)


def _adherence(history: list[HistoryEntry], today: date) -> PromptAdherence:
    """Recuentos para el prompt a partir del historial (ya con las no registradas calculadas)."""

    def since(first: date) -> list[HistoryEntry]:
        return [e for e in history if e.scheduled_at.date() >= first]

    last30 = since(today - timedelta(days=ADHERENCE_DAYS - 1))
    by_medication: dict[str, list[HistoryEntry]] = {}
    for entry in last30:
        by_medication.setdefault(entry.medication_name, []).append(entry)
    week_days = [today - timedelta(days=back) for back in range(6, -1, -1)]
    return PromptAdherence(
        week=_counts(since(week_days[0])),
        month=_counts(since(today.replace(day=1))),
        last30=_counts(last30),
        by_medication=[(name, _counts(rows)) for name, rows in sorted(by_medication.items())],
        by_day=[
            (day, _counts([e for e in history if e.scheduled_at.date() == day]))
            for day in week_days
        ],
    )


def _action_key(device_id: uuid.UUID, action_id: uuid.UUID) -> str:
    # Con el reloj en la clave, la propuesta de un reloj no la confirma otro.
    return f"chat:action:{device_id}:{action_id}"


_CHANGE_TYPES = ("update_schedule", "update_dosage", "archive_medication")

Draft = MedicationDraft | ScheduleDraft | TimesChangeDraft | DosageChangeDraft | ArchiveDraft
ActionType = Literal["schedule_updated", "dosage_updated", "medication_archived"]


def _when_times(times: list[str], days: list[int], lang: Lang) -> str:
    """'09:00 y 21:00 todos los días': solo la parte de horas y días de la descripción."""
    full = describe_medication("X", "Y", times, days, lang)
    return full.removeprefix("X Y a las ").removeprefix("X Y at ")


def _drafts(calls: list[ToolCall]) -> list[Draft]:
    """Las llamadas con argumentos válidos, en orden; las demás se ignoran."""
    models: dict[str, type[Draft]] = {
        "create_medication": MedicationDraft,
        "add_schedule": ScheduleDraft,
        "update_schedule": TimesChangeDraft,
        "update_dosage": DosageChangeDraft,
        "archive_medication": ArchiveDraft,
    }
    drafts: list[Draft] = []
    for call in calls:
        model = models.get(call.name)
        if model is None:
            continue
        try:
            drafts.append(model.model_validate_json(call.arguments))
        except ValidationError:
            continue
    return drafts


def _summary(create: DeviceMedicationCreate, lang: Lang) -> str:
    return describe_medication(
        create.name, create.dosage, create.schedule.times, create.schedule.days_of_week, lang
    )


async def _prepend(
    first: StreamEvent | None, rest: AsyncIterator[StreamEvent]
) -> AsyncIterator[StreamEvent]:
    if first is not None:
        yield first
    async for event in rest:
        yield event
