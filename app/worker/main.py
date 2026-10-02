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
from app.modules.notifications.alerts import MissedDoseAlerts
from app.modules.notifications.dispatcher import Dispatcher
from app.modules.notifications.senders import (
    ConsoleEmailSender,
    ResendEmailSender,
    Sender,
    TelegramSender,
)
from app.modules.reports.processor import ReportProcessor


def build_senders(settings: Settings, client: httpx.AsyncClient) -> dict[str, Sender]:
    if settings.email_provider == "resend":
        key = settings.resend_api_key
        if key is None:  # Settings ya lo impide; por si alguien construye el worker a mano
            raise RuntimeError("Falta RESEND_API_KEY")
        email: Sender = ResendEmailSender(client, key.get_secret_value(), settings.email_from)
    else:
        email = ConsoleEmailSender()
    senders: dict[str, Sender] = {"email": email}
    # Sin token de bot no se registra el canal: los mensajes de Telegram quedan pendientes.
    if settings.telegram_bot_token is not None:
        senders["telegram"] = TelegramSender(client, settings.telegram_bot_token.get_secret_value())
    return senders


async def startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    configure_logging(settings.log_level, json=settings.log_json)
    engine = create_engine(settings)
    client = httpx.AsyncClient(timeout=10)
    ctx["engine"] = engine
    ctx["http"] = client
    factory = create_session_factory(engine)
    ctx["dispatcher"] = Dispatcher(factory, build_senders(settings, client), settings)
    ctx["alerts"] = MissedDoseAlerts(factory, settings)
    ctx["reports"] = ReportProcessor(factory, settings)


async def shutdown(ctx: dict[str, Any]) -> None:
    await ctx["http"].aclose()
    await ctx["engine"].dispose()


async def deliver_outbox(ctx: dict[str, Any]) -> int:
    stats = await ctx["dispatcher"].deliver_due()
    return int(stats.processed)


async def check_missed_doses(ctx: dict[str, Any]) -> int:
    return int(await ctx["alerts"].run())


async def create_weekly_reports(ctx: dict[str, Any]) -> int:
    return int(await ctx["reports"].create_weekly())


async def process_reports(ctx: dict[str, Any]) -> int:
    return int(await ctx["reports"].process_pending())


class WorkerSettings:
    functions = [deliver_outbox, check_missed_doses, create_weekly_reports, process_reports]  # noqa: RUF012
    cron_jobs = [  # noqa: RUF012
        # Cada 15 s; los correos de cuenta no necesitan más inmediatez.
        cron(deliver_outbox, second={0, 15, 30, 45}, run_at_startup=True),
        # Cada minuto: una dosis pasa a "omitida" a los 60 min y el aviso no debe tardar mucho más.
        cron(check_missed_doses, second={20}),
        # Cada 10 s: la web espera el reporte que acaba de pedir.
        cron(process_reports, second={5, 15, 25, 35, 45, 55}),
        # Cada hora: crea el reporte semanal cuando llega el lunes 08:00 local de cada paciente.
        cron(create_weekly_reports, minute={0}, second={30}, run_at_startup=True),
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(str(get_settings().redis_url))
    max_jobs = 5
    job_timeout = 120
    keep_result = 0
    # arq registra señales con el loop de asyncio, que Windows no soporta.
    handle_signals = sys.platform != "win32"
