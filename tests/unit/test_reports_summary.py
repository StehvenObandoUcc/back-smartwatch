import uuid
from datetime import UTC, date, datetime

import pytest

from app.modules.doses.history import HistoryEntry
from app.modules.notifications.recipients import Recipient
from app.modules.notifications.templates import render_email, render_telegram
from app.modules.reports.summary import build_pdf, summarize

NOW = datetime(2026, 10, 12, 13, 0, tzinfo=UTC)


def _entry(med: uuid.UUID, name: str, status: str) -> HistoryEntry:
    return HistoryEntry(uuid.uuid4(), med, name, "1 tableta", NOW, status, None)


def test_summary_counts_overall_and_per_medication_sorted_by_name() -> None:
    zinc, aspirin = uuid.uuid4(), uuid.uuid4()
    entries = [
        _entry(zinc, "Zinc", "TAKEN"),
        _entry(aspirin, "aspirina", "TAKEN"),
        _entry(aspirin, "aspirina", "MISSED"),
        _entry(aspirin, "aspirina", "SKIPPED"),
    ]

    summary = summarize(entries)

    assert (summary.counts.taken, summary.counts.skipped, summary.counts.missed) == (2, 1, 1)
    assert [m.medication_name for m in summary.by_medication] == ["aspirina", "Zinc"]
    as_json = summary.to_json()
    assert as_json["percentage"] == 50.0
    assert as_json["byMedication"][0] == {
        "medicationId": str(aspirin),
        "medicationName": "aspirina",
        "taken": 1,
        "skipped": 1,
        "missed": 1,
        "percentage": 33.3,
    }


def test_summary_of_nothing_has_no_percentage() -> None:
    assert summarize([]).to_json() == {
        "taken": 0,
        "skipped": 0,
        "missed": 0,
        "percentage": None,
        "byMedication": [],
    }


@pytest.mark.parametrize("name", ["Ana Pérez", "Ana 🌸 田中", "x" * 80])
def test_pdf_is_a_real_document_for_any_name(name: str) -> None:
    med = uuid.uuid4()
    summary = summarize([_entry(med, name, "TAKEN"), _entry(med, name, "MISSED")])

    pdf = build_pdf(name, date(2026, 10, 5), date(2026, 10, 11), summary, NOW)

    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 500


def test_pdf_without_doses_still_builds() -> None:
    pdf = build_pdf("Ana", date(2026, 10, 5), date(2026, 10, 11), summarize([]), NOW)

    assert pdf.startswith(b"%PDF")


def _recipient(**overrides: object) -> Recipient:
    values: dict[str, object] = {
        "user_id": uuid.uuid4(),
        "email": "a@example.com",
        "email_verified": True,
        "chat_id": 5,
        "prefs": {
            "missed_dose_email": True,
            "missed_dose_telegram": True,
            "weekly_report_email": True,
            "weekly_report_telegram": True,
        },
        "consent": True,
    }
    return Recipient(**{**values, **overrides})  # type: ignore[arg-type]


def test_a_recipient_gets_every_linked_channel_they_allow() -> None:
    assert _recipient().channels("missed_dose") == ["email", "telegram"]
    assert _recipient(email_verified=False).channels("missed_dose") == ["telegram"]
    assert _recipient(chat_id=None).channels("weekly_report") == ["email"]
    assert _recipient(consent=False).channels("missed_dose") == []


def test_preferences_are_per_event_and_channel() -> None:
    prefs = {
        "missed_dose_email": False,
        "missed_dose_telegram": True,
        "weekly_report_email": True,
        "weekly_report_telegram": False,
    }

    recipient = _recipient(prefs=prefs)

    assert recipient.channels("missed_dose") == ["telegram"]
    assert recipient.channels("weekly_report") == ["email"]


def test_alert_and_report_messages_carry_a_minimal_summary_and_the_link() -> None:
    missed = {"patient_name": "Ana", "local_time": "08:00", "link": "https://web/x"}
    report = {
        "patient_name": "Ana",
        "link": "https://web/r",
        "percentage": 57.1,
        "taken": 4,
        "skipped": 1,
        "missed": 2,
        "period_start": "2026-10-05",
        "period_end": "2026-10-11",
    }

    assert "https://web/x" in render_email("missed_dose", missed)[1]
    assert "08:00" in render_email("missed_dose", missed)[1]
    assert "https://web/x" in render_telegram("missed_dose", missed)
    assert "08:00" not in render_telegram("missed_dose", missed)  # Telegram: sin horas
    assert "57,1 %" in render_email("weekly_report", report)[1]
    assert "https://web/r" in render_telegram("weekly_report", report)
    assert "sin datos" in render_telegram("weekly_report", {**report, "percentage": None})
