"""Resumen de adherencia de un periodo y su PDF. Funciones puras, sin I/O."""

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from fpdf import FPDF

from app.modules.doses.history import Counts, HistoryEntry, count


@dataclass(frozen=True, slots=True)
class MedicationSummary:
    medication_id: uuid.UUID
    medication_name: str
    dosage: str
    counts: Counts


@dataclass(frozen=True, slots=True)
class DaySummary:
    day: date
    counts: Counts


@dataclass(frozen=True, slots=True)
class Summary:
    counts: Counts
    by_medication: list[MedicationSummary]
    by_day: list[DaySummary]

    def to_json(self) -> dict[str, Any]:
        """Forma camelCase del contrato (`ReportSummary`), la que se guarda en la base."""
        return {
            **_counts_json(self.counts),
            "byMedication": [
                {
                    "medicationId": str(m.medication_id),
                    "medicationName": m.medication_name,
                    "dosage": m.dosage,
                    "scheduled": _scheduled(m.counts),
                    **_counts_json(m.counts),
                }
                for m in self.by_medication
            ],
            "byDay": [
                {
                    "date": d.day.isoformat(),
                    "taken": d.counts.taken,
                    "skipped": d.counts.skipped,
                    "missed": d.counts.missed,
                }
                for d in self.by_day
            ],
        }


def _scheduled(counts: Counts) -> int:
    return counts.taken + counts.skipped + counts.missed


def _counts_json(counts: Counts) -> dict[str, Any]:
    return {
        "taken": counts.taken,
        "skipped": counts.skipped,
        "missed": counts.missed,
        "percentage": counts.percentage,
    }


def summarize(entries: list[HistoryEntry], start: date, end: date) -> Summary:
    """Totales, por medicamento y por día (todos los días de `start` a `end`, aunque sin dosis)."""
    groups: dict[uuid.UUID, list[HistoryEntry]] = {}
    days: dict[date, list[HistoryEntry]] = {}
    for entry in entries:
        groups.setdefault(entry.medication_id, []).append(entry)
        days.setdefault(entry.scheduled_at.date(), []).append(entry)  # hora local del paciente
    by_medication = sorted(
        (
            MedicationSummary(
                med_id,
                group[0].medication_name,
                max(group, key=lambda e: e.scheduled_at).dosage,  # la dosis vigente al final
                count(group),
            )
            for med_id, group in groups.items()
        ),
        key=lambda m: m.medication_name.lower(),
    )
    by_day = [
        DaySummary(day, count(days.get(day, [])))
        for day in (start + timedelta(days=i) for i in range((end - start).days + 1))
    ]
    return Summary(counts=count(entries), by_medication=by_medication, by_day=by_day)


def _latin1(text: str) -> str:
    # Las fuentes base del PDF solo cubren latin-1: lo demás (emojis, otros alfabetos) pasa a "?".
    return text.encode("latin-1", "replace").decode("latin-1")


def _percentage(value: float | None) -> str:
    return "sin datos" if value is None else f"{value:.1f} %".replace(".", ",")


_SERIES = (
    ("taken", (46, 160, 67), "Tomadas"),
    ("skipped", (234, 179, 8), "Omitidas"),
    ("missed", (220, 53, 69), "Sin registrar"),
)


def _room(pdf: FPDF, height: float) -> None:
    """Pasa a otra página si el bloque que sigue no cabe, para no partir un gráfico."""
    if pdf.get_y() + height > pdf.h - pdf.b_margin:
        pdf.add_page()


def _title(pdf: FPDF, text: str) -> None:
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 9, text, new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 11)


def _legend(pdf: FPDF) -> None:
    for _, color, label in _SERIES:
        pdf.set_fill_color(*color)
        pdf.rect(pdf.get_x(), pdf.get_y() + 1, 4, 4, "F")
        pdf.set_x(pdf.get_x() + 6)
        pdf.cell(32, 6, label)
    pdf.ln(8)


def _day_chart(pdf: FPDF, days: list[DaySummary]) -> None:
    """Barras apiladas verticales: dosis tomadas, omitidas y sin registrar de cada día."""
    height = 42.0
    _room(pdf, height + 30)
    _title(pdf, "Dosis por dia")
    _legend(pdf)
    left, width, top = pdf.l_margin, pdf.epw, pdf.get_y() + 4
    peak = max((_scheduled(d.counts) for d in days), default=0) or 1
    slot = width / max(len(days), 1)
    bar = slot * 0.55
    base = top + height
    pdf.set_font("Helvetica", "", 8)
    for i, day in enumerate(days):
        x, y = left + i * slot + (slot - bar) / 2, base
        for key, color, _ in _SERIES:
            value: int = getattr(day.counts, key)
            if value:
                h = value / peak * height
                y -= h
                pdf.set_fill_color(*color)
                pdf.rect(x, y, bar, h, "F")
        total = _scheduled(day.counts)
        pdf.set_xy(left + i * slot, y - 4.5)
        pdf.cell(slot, 4, str(total) if total else "", align="C")
        pdf.set_xy(left + i * slot, base + 1)
        pdf.cell(slot, 4, f"{day.day:%d/%m}", align="C")
    pdf.set_draw_color(120, 120, 120)
    pdf.line(left, base, left + width, base)
    pdf.set_y(base + 9)
    pdf.set_font("Helvetica", "", 11)


def _medication_chart(pdf: FPDF, meds: list[MedicationSummary]) -> None:
    """Barras apiladas horizontales: dosis tomadas, omitidas y sin registrar de cada medicamento."""
    row = 8.0
    _room(pdf, row * len(meds) + 24)
    _title(pdf, "Dosis por medicamento")
    _legend(pdf)
    label_w, value_w = 62.0, 18.0
    left = pdf.l_margin
    max_w = pdf.epw - label_w - value_w
    peak = max((_scheduled(m.counts) for m in meds), default=0) or 1
    pdf.set_font("Helvetica", "", 9)
    for med in meds:
        y = pdf.get_y()
        pdf.set_xy(left, y)
        pdf.cell(label_w, row - 1, _latin1(f"{med.medication_name} {med.dosage}")[:34])
        x = left + label_w
        for key, color, _ in _SERIES:
            value: int = getattr(med.counts, key)
            if value:
                w = value / peak * max_w
                pdf.set_fill_color(*color)
                pdf.rect(x, y + 1, w, row - 3, "F")
                x += w
        pdf.set_xy(x + 2, y)
        pdf.cell(value_w, row - 1, f"{med.counts.taken}/{_scheduled(med.counts)}")
        pdf.set_y(y + row)
    pdf.set_font("Helvetica", "", 11)


def build_pdf(
    patient_name: str, start: date, end: date, summary: Summary, generated_at: datetime
) -> bytes:
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 12, "Reporte semanal de adherencia", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 12)
    pdf.cell(0, 8, _latin1(f"Paciente: {patient_name}"), new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 8, f"Periodo: {start:%d/%m/%Y} a {end:%d/%m/%Y}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    c = summary.counts
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 10, f"Adherencia: {_percentage(c.percentage)}", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 12)
    pdf.cell(
        0,
        8,
        f"Tomadas: {c.taken}   Omitidas por el paciente: {c.skipped}   Sin registrar: {c.missed}",
        new_x="LMARGIN",
        new_y="NEXT",
    )
    pdf.ln(4)

    if summary.by_medication:
        _title(pdf, "Detalle por medicamento")
        with pdf.table(
            col_widths=(58, 24, 18, 20, 20, 18),
            text_align=("LEFT", "CENTER", "CENTER", "CENTER", "CENTER", "CENTER"),
        ) as table:
            header = table.row()
            for title in ("Medicamento", "Tomo", "Omitidas", "Sin reg.", "Programadas", "%"):
                header.cell(title, style=None)
            for med in summary.by_medication:
                total = _scheduled(med.counts)
                row = table.row()
                row.cell(_latin1(f"{med.medication_name} {med.dosage}"))
                row.cell(f"{med.counts.taken} de {total}")
                row.cell(str(med.counts.skipped))
                row.cell(str(med.counts.missed))
                row.cell(str(total))
                row.cell(_percentage(med.counts.percentage))
        pdf.ln(4)
        for med in summary.by_medication:
            total = _scheduled(med.counts)
            line = f"{med.medication_name} {med.dosage}: tomo {med.counts.taken} de {total} dosis."
            pdf.multi_cell(0, 6, _latin1(line), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(4)
        _day_chart(pdf, summary.by_day)
        _medication_chart(pdf, summary.by_medication)
    else:
        pdf.cell(0, 8, "No hubo dosis programadas en el periodo.", new_x="LMARGIN", new_y="NEXT")

    pdf.ln(6)
    pdf.set_font("Helvetica", "I", 9)
    pdf.multi_cell(
        0,
        5,
        f"Generado el {generated_at:%d/%m/%Y %H:%M} UTC. Informativo: no sustituye la opinion "
        "de un profesional de la salud. 'Sin registrar' son dosis que pasaron 60 minutos sin "
        "que el reloj registrara una toma.",
    )
    return bytes(pdf.output())
