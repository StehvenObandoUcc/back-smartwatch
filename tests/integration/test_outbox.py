"""Entrega del outbox con proveedores simulados: reintentos, backoff y concurrencia."""

import asyncio
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings
from app.core.db import create_session_factory
from app.core.security import utcnow
from app.modules.notifications.dispatcher import Dispatcher
from app.modules.notifications.repository import OutboxRepository
from app.modules.notifications.senders import DeliveryError, Message, Sender


class FakeSender:
    """Anota lo que recibe; `failures` se consume en orden (una excepción por intento)."""

    def __init__(self, failures: list[Exception] | None = None, delay: float = 0) -> None:
        self.sent: list[Message] = []
        self._failures = failures or []
        self._delay = delay

    async def send(self, message: Message) -> None:
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._failures:
            raise self._failures.pop(0)
        self.sent.append(message)


def _dispatcher(
    engine: AsyncEngine, settings: Settings, senders: dict[str, Sender], **overrides: Any
) -> Dispatcher:
    return Dispatcher(
        create_session_factory(engine), senders, settings.model_copy(update=overrides)
    )


async def _enqueue(
    engine: AsyncEngine, key: str, channel: str = "email", kind: str = "verify_email"
) -> None:
    async with create_session_factory(engine)() as session:
        await OutboxRepository(session).enqueue(
            channel=channel,
            kind=kind,
            payload={"to": "a@example.com", "link": "https://web.example/x?token=secreto"},
            dedupe_key=key,
        )
        await session.commit()


async def _row(engine: AsyncEngine, key: str) -> dict[str, Any]:
    async with engine.connect() as conn:
        result = await conn.execute(
            text("SELECT * FROM notifications_outbox WHERE dedupe_key = :k"), {"k": key}
        )
        return dict(result.one()._mapping)


async def test_enqueue_is_idempotent_by_dedupe_key(db_engine: AsyncEngine) -> None:
    async with create_session_factory(db_engine)() as session:
        repo = OutboxRepository(session)
        first = await repo.enqueue(channel="email", kind="verify_email", payload={}, dedupe_key="k")
        second = await repo.enqueue(
            channel="email", kind="verify_email", payload={}, dedupe_key="k"
        )
        await session.commit()

    assert (first, second) == (True, False)
    async with db_engine.connect() as conn:
        count = await conn.scalar(text("SELECT count(*) FROM notifications_outbox"))
    assert count == 1


async def test_sent_message_is_marked_and_its_tokens_scrubbed(
    db_engine: AsyncEngine, integration_settings: Settings
) -> None:
    await _enqueue(db_engine, "a")
    sender = FakeSender()

    stats = await _dispatcher(db_engine, integration_settings, {"email": sender}).deliver_due()

    assert (stats.sent, stats.retried, stats.failed) == (1, 0, 0)
    assert sender.sent[0].payload["link"].endswith("token=secreto")
    row = await _row(db_engine, "a")
    assert (row["status"], row["attempts"], row["payload"]) == ("sent", 1, {})
    assert row["sent_at"] is not None


async def test_payload_is_kept_when_scrubbing_is_off(
    db_engine: AsyncEngine, integration_settings: Settings
) -> None:
    await _enqueue(db_engine, "a")

    await _dispatcher(
        db_engine, integration_settings, {"email": FakeSender()}, outbox_scrub_payload=False
    ).deliver_due()

    assert (await _row(db_engine, "a"))["payload"]["to"] == "a@example.com"


async def test_a_sent_message_is_never_sent_again(
    db_engine: AsyncEngine, integration_settings: Settings
) -> None:
    await _enqueue(db_engine, "a")
    sender = FakeSender()
    dispatcher = _dispatcher(db_engine, integration_settings, {"email": sender})

    await dispatcher.deliver_due()
    again = await dispatcher.deliver_due(utcnow() + timedelta(days=1))

    assert again.processed == 0
    assert len(sender.sent) == 1


async def test_transient_failure_backs_off_then_succeeds(
    db_engine: AsyncEngine, integration_settings: Settings
) -> None:
    await _enqueue(db_engine, "a")
    sender = FakeSender([DeliveryError("resend: http 503", retryable=True)])
    dispatcher = _dispatcher(db_engine, integration_settings, {"email": sender})

    first = await dispatcher.deliver_due()
    row = await _row(db_engine, "a")
    immediately = await dispatcher.deliver_due()
    later = await dispatcher.deliver_due(utcnow() + timedelta(minutes=5))

    assert (first.retried, first.sent) == (1, 0)
    assert (row["status"], row["attempts"], row["last_error"]) == (
        "pending",
        1,
        "resend: http 503",
    )
    assert row["next_attempt_at"] > utcnow()
    assert immediately.processed == 0
    assert later.sent == 1
    assert (await _row(db_engine, "a"))["status"] == "sent"


async def test_backoff_doubles_each_attempt(
    db_engine: AsyncEngine, integration_settings: Settings
) -> None:
    await _enqueue(db_engine, "a")
    sender = FakeSender([DeliveryError("x", retryable=True)] * 3)
    dispatcher = _dispatcher(
        db_engine, integration_settings, {"email": sender}, outbox_retry_base_seconds=60
    )
    delays = []
    now = utcnow()
    for _ in range(3):
        await dispatcher.deliver_due(now)
        row = await _row(db_engine, "a")
        delays.append(round((row["next_attempt_at"] - utcnow()).total_seconds(), -1))
        now = row["next_attempt_at"] + timedelta(seconds=1)

    assert delays == [60.0, 120.0, 240.0]


async def test_permanent_failure_is_not_retried(
    db_engine: AsyncEngine, integration_settings: Settings
) -> None:
    await _enqueue(db_engine, "a")
    sender = FakeSender([DeliveryError("resend: http 422", retryable=False)])
    dispatcher = _dispatcher(db_engine, integration_settings, {"email": sender})

    stats = await dispatcher.deliver_due()
    again = await dispatcher.deliver_due(utcnow() + timedelta(days=1))

    assert stats.failed == 1
    assert again.processed == 0
    row = await _row(db_engine, "a")
    assert (row["status"], row["attempts"], row["payload"]) == ("failed", 1, {})


async def test_gives_up_after_the_maximum_attempts(
    db_engine: AsyncEngine, integration_settings: Settings
) -> None:
    await _enqueue(db_engine, "a")
    sender = FakeSender([DeliveryError("x", retryable=True)] * 5)
    dispatcher = _dispatcher(
        db_engine, integration_settings, {"email": sender}, outbox_max_attempts=2
    )

    await dispatcher.deliver_due()
    final = await dispatcher.deliver_due(utcnow() + timedelta(hours=2))

    assert final.failed == 1
    assert (await _row(db_engine, "a"))["status"] == "failed"


async def test_unexpected_errors_are_retried_without_leaking_their_message(
    db_engine: AsyncEngine, integration_settings: Settings
) -> None:
    await _enqueue(db_engine, "a")
    sender = FakeSender([RuntimeError("a@example.com token=secreto")])

    await _dispatcher(db_engine, integration_settings, {"email": sender}).deliver_due()

    row = await _row(db_engine, "a")
    assert (row["status"], row["last_error"]) == ("pending", "RuntimeError")


async def test_channels_without_a_sender_are_left_pending(
    db_engine: AsyncEngine, integration_settings: Settings
) -> None:
    await _enqueue(db_engine, "tg", channel="telegram", kind="weekly_report")

    stats = await _dispatcher(db_engine, integration_settings, {"email": FakeSender()}).deliver_due(
        utcnow() + timedelta(days=1)
    )

    assert stats.processed == 0
    assert (await _row(db_engine, "tg"))["status"] == "pending"


async def test_concurrent_workers_never_send_the_same_message_twice(
    db_engine: AsyncEngine, integration_settings: Settings
) -> None:
    for index in range(6):
        await _enqueue(db_engine, f"m{index}")
    sender = FakeSender(delay=0.05)
    workers = [_dispatcher(db_engine, integration_settings, {"email": sender}) for _ in range(3)]

    results = await asyncio.gather(*(worker.deliver_due() for worker in workers))

    assert sum(r.sent for r in results) == 6
    assert sorted(m.dedupe_key for m in sender.sent) == [f"m{i}" for i in range(6)]


@pytest.mark.parametrize("batch", [2])
async def test_batch_size_limits_one_run(
    db_engine: AsyncEngine, integration_settings: Settings, batch: int
) -> None:
    for index in range(5):
        await _enqueue(db_engine, f"m{index}")
    dispatcher = _dispatcher(
        db_engine, integration_settings, {"email": FakeSender()}, outbox_batch_size=batch
    )

    assert (await dispatcher.deliver_due()).sent == batch
    assert (await dispatcher.deliver_due()).sent == batch
    assert (await dispatcher.deliver_due()).sent == 1
