"""Worker de arq: entrega el outbox (correo y, después, Telegram) y las tareas programadas.

Arranque: `uv run python -m arq app.worker.main.WorkerSettings`.
"""

import sys
from typing import Any

import httpx
from arq import cron
from arq.connections import RedisSettings

from app.core.config import Settings, get_settings
from app.core.db import create_engine, create_session_factory
from app.core.logging import configure_logging
from app.modules.notifications.dispatcher import Dispatcher
from app.modules.notifications.senders import ConsoleEmailSender, ResendEmailSender, Sender


def build_senders(settings: Settings, client: httpx.AsyncClient) -> dict[str, Sender]:
    if settings.email_provider == "resend":
        key = settings.resend_api_key
        if key is None:  # Settings ya lo impide; por si alguien construye el worker a mano
            raise RuntimeError("Falta RESEND_API_KEY")
        email: Sender = ResendEmailSender(client, key.get_secret_value(), settings.email_from)
    else:
        email = ConsoleEmailSender()
    return {"email": email}


async def startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    configure_logging(settings.log_level, json=settings.log_json)
    engine = create_engine(settings)
    client = httpx.AsyncClient(timeout=10)
    ctx["engine"] = engine
    ctx["http"] = client
    ctx["dispatcher"] = Dispatcher(
        create_session_factory(engine), build_senders(settings, client), settings
    )


async def shutdown(ctx: dict[str, Any]) -> None:
    await ctx["http"].aclose()
    await ctx["engine"].dispose()


async def deliver_outbox(ctx: dict[str, Any]) -> int:
    stats = await ctx["dispatcher"].deliver_due()
    return int(stats.processed)


class WorkerSettings:
    functions = [deliver_outbox]  # noqa: RUF012
    # Cada 15 s; los correos de cuenta no necesitan más inmediatez.
    cron_jobs = [cron(deliver_outbox, second={0, 15, 30, 45}, run_at_startup=True)]  # noqa: RUF012
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(str(get_settings().redis_url))
    max_jobs = 5
    job_timeout = 120
    keep_result = 0
    # arq registra señales con el loop de asyncio, que Windows no soporta.
    handle_signals = sys.platform != "win32"
