"""Alerta de dosis omitida: avisa a los cuidadores cuando pasan 60 minutos sin registrar una toma.

Corre en el worker cada minuto. Mira las dosis de las últimas `lookback` horas (más viejas ya no
sirven de alerta) y encola un aviso por dosis, cuidador y canal. `dedupe_key` hace idempotente el
encolado: repetir la pasada no duplica nada. Mensajes con resumen mínimo: sin medicamentos ni
dosis, solo el nombre del paciente y un enlace al panel.
"""

import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.logging import get_logger
from app.core.security import utcnow
from app.modules.doses.loader import load_history
from app.modules.notifications.recipients import resolve_recipients
from app.modules.notifications.repository import OutboxRepository
from app.modules.plan.generator import local_today
from app.modules.reports.repository import ReportRepository

logger = get_logger(__name__)


class MissedDoseAlerts:
    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], settings: Settings
    ) -> None:
        self._factory = session_factory
        self._web = settings.web_origin
        self._lookback = timedelta(hours=settings.missed_alert_lookback_hours)

    async def run(self, now: datetime | None = None) -> int:
        """Encola las alertas pendientes; devuelve cuántas filas nuevas entraron al outbox."""
        now = now or utcnow()
        async with self._factory() as session:
            patients = await ReportRepository(session).patients_with_schedules()
        queued = 0
        for patient in patients:
            # Una transacción por paciente: un fallo no frena a los demás.
            async with self._factory() as session:
                queued += await self._patient(session, patient.id, now)
        return queued

    async def _patient(self, session: AsyncSession, patient_id: uuid.UUID, now: datetime) -> int:
        patient = await ReportRepository(session).get_patient(patient_id)
        if patient is None:
            return 0
        tz = ZoneInfo(patient.timezone)
        today = local_today(tz, now)
        entries = await load_history(session, patient, today - timedelta(days=1), today, now)
        missed = [
            e for e in entries if e.status == "MISSED" and e.scheduled_at >= now - self._lookback
        ]
        if not missed:
            return 0
        recipients = await resolve_recipients(session, patient, include_owner=False)
        outbox = OutboxRepository(session)
        queued = 0
        for entry in missed:
            instant = entry.scheduled_at.astimezone(UTC).strftime("%Y%m%dT%H%MZ")
            for recipient in recipients:
                for channel in recipient.channels("missed_dose"):
                    payload: dict[str, object] = {
                        "patient_name": patient.display_name,
                        "link": f"{self._web}/patients/{patient.id}",
                    }
                    if channel == "email":
                        payload["to"] = recipient.email
                        payload["local_time"] = entry.scheduled_at.strftime("%H:%M")
                    else:
                        payload["chat_id"] = recipient.chat_id
                    queued += await outbox.enqueue(
                        channel=channel,
                        kind="missed_dose",
                        payload=payload,
                        dedupe_key=f"missed_dose:{entry.schedule_id}:{instant}:{recipient.user_id}:{channel}",
                    )
        await session.commit()
        if queued:
            logger.info("missed_dose_alerts_queued", patient_id=str(patient.id), count=queued)
        return queued
