"""Entrega del outbox: toma mensajes pendientes, los envía y registra el resultado.

Un mensaje por transacción con `FOR UPDATE SKIP LOCKED`: varios workers no se pisan y el bloqueo
de un mensaje no retiene a los demás. Entrega "al menos una vez": si el proceso cae entre enviar
y confirmar, el mensaje se reenvía (los proveedores deduplican con `dedupe_key`).
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.logging import get_logger
from app.core.security import utcnow
from app.modules.notifications.models import OutboxMessage
from app.modules.notifications.senders import DeliveryError, Message, Sender

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class DeliveryStats:
    sent: int = 0
    retried: int = 0
    failed: int = 0

    @property
    def processed(self) -> int:
        return self.sent + self.retried + self.failed


class Dispatcher:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        senders: dict[str, Sender],
        settings: Settings,
    ) -> None:
        self._factory = session_factory
        self._senders = senders
        self._max_attempts = settings.outbox_max_attempts
        self._batch = settings.outbox_batch_size
        self._retry_base = settings.outbox_retry_base_seconds
        self._retry_max = settings.outbox_retry_max_seconds
        self._scrub = settings.outbox_scrub_payload

    async def deliver_due(self, now: datetime | None = None) -> DeliveryStats:
        now = now or utcnow()
        sent = retried = failed = 0
        for _ in range(self._batch):
            async with self._factory() as session:
                row = await self._next(session, now)
                if row is None:
                    break
                outcome = await self._deliver(row)
                await session.commit()
            sent += outcome == "sent"
            retried += outcome == "retry"
            failed += outcome == "failed"
        return DeliveryStats(sent, retried, failed)

    async def _next(self, session: AsyncSession, now: datetime) -> OutboxMessage | None:
        result = await session.execute(
            select(OutboxMessage)
            .where(
                OutboxMessage.status == "pending",
                OutboxMessage.next_attempt_at <= now,
                OutboxMessage.channel.in_(self._senders),
            )
            .order_by(OutboxMessage.next_attempt_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        return result.scalar_one_or_none()

    async def _deliver(self, row: OutboxMessage) -> str:
        message = Message(kind=row.kind, payload=row.payload, dedupe_key=row.dedupe_key)
        row.attempts += 1
        try:
            await self._senders[row.channel].send(message)
        except DeliveryError as exc:
            return self._failed(row, exc.reason, retryable=exc.retryable)
        except Exception as exc:
            # Error inesperado del proveedor: solo el tipo, el mensaje podría traer datos.
            return self._failed(row, type(exc).__name__, retryable=True)
        row.status = "sent"
        row.sent_at = utcnow()
        row.last_error = None
        self._scrub_payload(row)
        logger.info("outbox_sent", kind=row.kind, channel=row.channel, attempts=row.attempts)
        return "sent"

    def _failed(self, row: OutboxMessage, reason: str, *, retryable: bool) -> str:
        row.last_error = reason[:500]
        if not retryable or row.attempts >= self._max_attempts:
            row.status = "failed"
            self._scrub_payload(row)
            logger.error("outbox_failed", kind=row.kind, channel=row.channel, reason=reason)
            return "failed"
        delay = min(self._retry_base * 2 ** (row.attempts - 1), self._retry_max)
        row.next_attempt_at = utcnow() + timedelta(seconds=delay)
        logger.warning(
            "outbox_retry", kind=row.kind, channel=row.channel, attempts=row.attempts, reason=reason
        )
        return "retry"

    def _scrub_payload(self, row: OutboxMessage) -> None:
        # Los enlaces llevan tokens: entregado (o perdido) el mensaje, no hace falta guardarlos.
        if self._scrub:
            row.payload = {}
