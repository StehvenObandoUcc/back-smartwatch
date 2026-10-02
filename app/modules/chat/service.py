"""Chat con IA sobre el plan del paciente (web por SSE, reloj con respuesta corta).

Orden de las comprobaciones: sesión → acceso al paciente (404) → consentimientos `health_data` y
`ai_chat` (403) → límite por minuto (429) → cupo diario (429) → proveedor (503). Un fallo del
proveedor devuelve el mensaje reservado. Del texto de la conversación no se guarda ni se registra
nada: solo contadores de mensajes y tokens.
"""

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.auth import CurrentDevice, CurrentUser
from app.core.config import Settings
from app.core.errors import DomainError
from app.core.logging import get_logger
from app.core.rate_limit import RateLimiter
from app.core.security import utcnow
from app.modules.chat.prompt import PromptDose, build_system_prompt, shorten_for_watch
from app.modules.chat.provider import ChatProvider, ProviderError, StreamEvent, Turn, Usage
from app.modules.chat.repository import ChatUsageRepository
from app.modules.chat.schemas import ChatRequest, WatchChatReply
from app.modules.consents.service import ConsentService
from app.modules.doses.loader import load_history
from app.modules.patients.models import Patient
from app.modules.patients.service import PatientService, patient_not_found
from app.modules.plan.generator import day_start, doses_in_range, local_today
from app.modules.plan.repository import PlanRepository

logger = get_logger(__name__)

_STATUS_LABELS = {"TAKEN": "tomada", "SKIPPED": "omitida", "MISSED": "sin registrar"}


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
    ) -> None:
        self._session = session
        self._factory = session_factory
        self._settings = settings
        self._provider = provider
        self._limiter = limiter
        self._patients = PatientService(session)
        self._consents = ConsentService(session)
        self._plan = PlanRepository(session)
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
        return build_system_prompt(now.astimezone(tz), doses, short=short)

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

    async def reply_watch(self, device: CurrentDevice, data: ChatRequest) -> WatchChatReply:
        patient = await self._plan.get_patient(device.patient_id)
        if patient is None:
            raise patient_not_found()
        await self._require_consents(patient.id)
        await self._limiter.hit("chat:principal", str(device.id), self._settings.rate_chat_user)
        now = utcnow()
        system = await self._prompt(patient, now, short=True)
        day, remaining = await self._reserve(device.id, patient, now)
        text: list[str] = []
        usage: Usage | None = None
        try:
            async for event in self._provider.stream(
                system, self._turns(data), max_tokens=self._settings.chat_watch_max_tokens
            ):
                if event.text:
                    text.append(event.text)
                if event.usage:
                    usage = event.usage
        except ProviderError as exc:
            logger.warning("chat_provider_failed", reason=exc.reason)
            await self._release(device.id, day)
            raise ChatUnavailableError from None
        reply = shorten_for_watch("".join(text))
        if not reply:
            await self._release(device.id, day)
            raise ChatUnavailableError
        await self._record_tokens(device.id, day, usage, system, reply)
        return WatchChatReply(reply=reply, remaining_messages=remaining)


async def _prepend(
    first: StreamEvent | None, rest: AsyncIterator[StreamEvent]
) -> AsyncIterator[StreamEvent]:
    if first is not None:
        yield first
    async for event in rest:
        yield event
