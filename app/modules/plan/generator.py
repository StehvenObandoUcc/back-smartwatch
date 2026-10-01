"""Generación de dosis a partir de horarios simples. Funciones puras, sin I/O."""

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

PLAN_DAYS = 7


@dataclass(frozen=True, slots=True)
class ScheduleRule:
    schedule_id: uuid.UUID
    medication_id: uuid.UUID
    medication_name: str
    dosage: str
    instructions: str | None
    color: str
    times: tuple[time, ...]
    days_of_week: frozenset[int]
    start_date: date
    end_date: date | None
    effective_from: datetime
    medication_archived_at: datetime | None


@dataclass(frozen=True, slots=True)
class Dose:
    rule: ScheduleRule
    scheduled_at: datetime  # con el desfase de la zona del paciente


def parse_times(values: list[str]) -> tuple[time, ...]:
    return tuple(time.fromisoformat(value) for value in values)


def doses_in_range(
    rule: ScheduleRule, tz: ZoneInfo, first_day: date, last_day: date
) -> Iterator[Dose]:
    """Dosis de la regla entre dos fechas locales (ambas inclusive), en orden cronológico."""
    day = max(first_day, rule.start_date)
    stop = last_day if rule.end_date is None else min(last_day, rule.end_date)
    while day <= stop:
        if day.isoweekday() in rule.days_of_week:
            for at in rule.times:
                yield Dose(rule, datetime.combine(day, at, tzinfo=tz))
        day += timedelta(days=1)


def local_today(tz: ZoneInfo, now: datetime) -> date:
    return now.astimezone(tz).date()


def day_start(tz: ZoneInfo, day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=tz)


def plan_doses(rules: list[ScheduleRule], tz: ZoneInfo, now: datetime) -> list[Dose]:
    """Dosis de los próximos 7 días desde el inicio del día local de `now`, por hora."""
    first = local_today(tz, now)
    last = first + timedelta(days=PLAN_DAYS - 1)
    doses = [dose for rule in rules for dose in doses_in_range(rule, tz, first, last)]
    return sorted(doses, key=lambda d: d.scheduled_at.astimezone(UTC))
