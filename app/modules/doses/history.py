"""Historial y adherencia a partir de eventos guardados y horarios. Funciones puras, sin I/O.

`MISSED` no se guarda: una dosis sin evento cuya hora pasó hace más de `MISSED_AFTER` se calcula
al consultar. Solo cuentan las dosis que el horario ya generaba (`effective_from`) y mientras el
medicamento no estaba archivado.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.modules.plan.generator import ScheduleRule, doses_in_range

MISSED_AFTER = timedelta(minutes=60)
MAX_RANGE_DAYS = 90
DEFAULT_RANGE_DAYS = 7


@dataclass(frozen=True, slots=True)
class EventRecord:
    schedule_id: uuid.UUID
    medication_id: uuid.UUID
    medication_name: str
    dosage: str
    scheduled_at: datetime
    status: str  # TAKEN | SKIPPED
    acted_at: datetime


@dataclass(frozen=True, slots=True)
class HistoryEntry:
    schedule_id: uuid.UUID
    medication_id: uuid.UUID
    medication_name: str
    dosage: str
    scheduled_at: datetime  # en la zona del paciente
    status: str  # TAKEN | SKIPPED | MISSED
    acted_at: datetime | None


@dataclass(frozen=True, slots=True)
class Counts:
    taken: int
    skipped: int
    missed: int

    @property
    def percentage(self) -> float | None:
        total = self.taken + self.skipped + self.missed
        return round(self.taken / total * 100, 1) if total else None


def default_range(today: date) -> tuple[date, date]:
    return today - timedelta(days=DEFAULT_RANGE_DAYS - 1), today


def sort_key(entry: HistoryEntry) -> tuple[datetime, uuid.UUID]:
    return entry.scheduled_at.astimezone(UTC), entry.schedule_id


def build_history(
    rules: list[ScheduleRule],
    events: list[EventRecord],
    tz: ZoneInfo,
    first_day: date,
    last_day: date,
    now: datetime,
) -> list[HistoryEntry]:
    """Eventos del rango más las dosis omitidas, de la más reciente a la más antigua."""
    entries = [
        HistoryEntry(
            e.schedule_id,
            e.medication_id,
            e.medication_name,
            e.dosage,
            e.scheduled_at.astimezone(tz),
            e.status,
            e.acted_at,
        )
        for e in events
    ]
    recorded = {(e.schedule_id, e.scheduled_at.astimezone(UTC)) for e in events}
    cutoff = now - MISSED_AFTER
    for rule in rules:
        for dose in doses_in_range(rule, tz, first_day, last_day):
            at = dose.scheduled_at
            if at > cutoff or at < rule.effective_from:
                continue
            if rule.medication_archived_at is not None and at >= rule.medication_archived_at:
                continue
            if (rule.schedule_id, at.astimezone(UTC)) in recorded:
                continue
            entries.append(
                HistoryEntry(
                    rule.schedule_id,
                    rule.medication_id,
                    rule.medication_name,
                    rule.dosage,
                    at,
                    "MISSED",
                    None,
                )
            )
    return sorted(entries, key=sort_key, reverse=True)


def count(entries: list[HistoryEntry]) -> Counts:
    statuses = [e.status for e in entries]
    return Counts(
        taken=statuses.count("TAKEN"),
        skipped=statuses.count("SKIPPED"),
        missed=statuses.count("MISSED"),
    )
