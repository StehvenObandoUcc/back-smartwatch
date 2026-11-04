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
from typing import Any
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
    ADD_SCHEDULE_TOOL,
    CREATE_MEDICATION_TOOL,
    MAX_MEDICATIONS_IN_CONTEXT,
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
    ChatAction,
    ChatRequest,
    MedicationDraft,
    PendingAction,
    ScheduleDraft,
    ScheduleProposal,
    WatchChatReply,
    WatchChatRequest,
)
from app.modules.consents.service import ConsentService
from app.modules.doses.loader import load_history
from app.modules.medications.repository import MedicationRepository
from app.modules.medications.schemas import DeviceMedicationCreate, ScheduleCreate
from app.modules.medications.service import MedicationService
from app.modules.patients.models import Patient
from app.modules.patients.service import PatientService, patient_not_found
from app.modules.plan.generator import day_start, doses_in_range, local_today
from app.modules.plan.repository import PlanRepository

logger = get_logger(__name__)

_STATUS_LABELS = {"TAKEN": "tomada", "SKIPPED": "omitida", "MISSED": "sin registrar"}
PENDING_ACTION_TTL_SECONDS = 600


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
        rules = await self._plan.rules(
            patient.id, today, today + timedelta(days=1), include_archived=False
        )
        done = {
            (e.schedule_id, e.scheduled_at.astimezone(UTC)): _STATUS_LABELS[e.status]
            for e in await load_history(self._session, patient, today, today, now)
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
                for dose in doses_in_range(rule, tz, today, today + timedelta(days=1))
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
        return build_system_prompt(now.astimezone(tz), doses, short=short, medications=medications)

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
        if data.confirm_action_id is not None:
            return await self._confirm(device.id, patient, data.confirm_action_id, now)
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
                tools=[CREATE_MEDICATION_TOOL, ADD_SCHEDULE_TOOL],
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
                pending = await self._propose(device.id, patient, draft, now)
                reply = f"Voy a agregar {pending.summary}. ¿Lo confirmas?"
                break
            pending = await self._propose_schedule(device.id, patient, draft, now)
            if pending is not None:
                reply = f"{pending.summary}. ¿Lo confirmas?"
                break
        if pending is None and calls:  # pidió agregar algo, pero los datos no son válidos
            reply = "No entendí bien el medicamento o la hora. ¿Me lo repites?"
        elif pending is None:
            reply = shorten_for_watch("".join(text))
        if not reply:
            await self._release(device.id, day)
            raise ChatUnavailableError
        await self._record_tokens(device.id, day, usage, system, "".join(text))
        return WatchChatReply(reply=reply, remaining_messages=remaining, pending_action=pending)

    async def _propose(
        self, device_id: uuid.UUID, patient: Patient, draft: MedicationDraft, now: datetime
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
        return PendingAction(id=action_id, summary=_summary(create))

    async def _confirm(
        self, device_id: uuid.UUID, patient: Patient, action_id: uuid.UUID, now: datetime
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
        if stored.get("type") == "add_schedule":
            return await self._confirm_schedule(
                device_id, patient, action_id, ScheduleProposal.model_validate(stored), remaining
            )
        create = DeviceMedicationCreate.model_validate(stored)
        created, _ = await MedicationService(self._session).create_from_device(patient.id, create)
        return WatchChatReply(
            reply=f"Listo, agregué {_summary(create)}.",
            remaining_messages=remaining,
            actions=[
                ChatAction(medication_id=created.medication.id, schedule_id=created.schedule.id)
            ],
        )

    async def _propose_schedule(
        self, device_id: uuid.UUID, patient: Patient, draft: ScheduleDraft, now: datetime
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
                medication.name, medication.dosage, draft.times, draft.days_of_week
            ),
        )
        await self._save(device_id, action_id, proposal, PENDING_ACTION_TTL_SECONDS)
        return PendingAction(
            id=action_id, type="add_schedule", summary=f"Agregar {proposal.description}"
        )

    async def _confirm_schedule(
        self,
        device_id: uuid.UUID,
        patient: Patient,
        action_id: uuid.UUID,
        proposal: ScheduleProposal,
        remaining: int,
    ) -> WatchChatReply:
        schedule_id = proposal.schedule_id
        if schedule_id is None:
            # ponytail: dos confirmaciones simultáneas de la misma propuesta crearían dos
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
            reply=f"Listo, agregué {proposal.description}.",
            remaining_messages=remaining,
            actions=[
                ChatAction(
                    type="schedule_added",
                    medication_id=proposal.medication_id,
                    schedule_id=schedule_id,
                )
            ],
        )

    async def _save(
        self, device_id: uuid.UUID, action_id: uuid.UUID, proposal: ScheduleProposal, ttl: int
    ) -> None:
        await self._redis.set(
            _action_key(device_id, action_id), proposal.model_dump_json(by_alias=True), ex=ttl
        )


def _action_key(device_id: uuid.UUID, action_id: uuid.UUID) -> str:
    # Con el reloj en la clave, la propuesta de un reloj no la confirma otro.
    return f"chat:action:{device_id}:{action_id}"


def _drafts(calls: list[ToolCall]) -> list[MedicationDraft | ScheduleDraft]:
    """Las llamadas con argumentos válidos, en orden; las demás se ignoran."""
    models: dict[str, type[MedicationDraft] | type[ScheduleDraft]] = {
        "create_medication": MedicationDraft,
        "add_schedule": ScheduleDraft,
    }
    drafts: list[MedicationDraft | ScheduleDraft] = []
    for call in calls:
        model = models.get(call.name)
        if model is None:
            continue
        try:
            drafts.append(model.model_validate_json(call.arguments))
        except ValidationError:
            continue
    return drafts


def _summary(create: DeviceMedicationCreate) -> str:
    return describe_medication(
        create.name, create.dosage, create.schedule.times, create.schedule.days_of_week
    )


async def _prepend(
    first: StreamEvent | None, rest: AsyncIterator[StreamEvent]
) -> AsyncIterator[StreamEvent]:
    if first is not None:
        yield first
    async for event in rest:
        yield event
