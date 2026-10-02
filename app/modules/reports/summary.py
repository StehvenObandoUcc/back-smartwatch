"""Resumen de adherencia de un periodo y su PDF. Funciones puras, sin I/O."""

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from fpdf import FPDF

from app.modules.doses.history import Counts, HistoryEntry, count


@dataclass(frozen=True, slots=True)
class MedicationSummary:
    medication_id: uuid.UUID
    medication_name: str
    counts: Counts


@dataclass(frozen=True, slots=True)
class Summary:
    counts: Counts
    by_medication: list[MedicationSummary]

    def to_json(self) -> dict[str, Any]:
        """Forma camelCase del contrato (`ReportSummary`), la que se guarda en la base."""
        return {
            **_counts_json(self.counts),
            "byMedication": [
                {
                    "medicationId": str(m.medication_id),
                    "medicationName": m.medication_name,
                    **_counts_json(m.counts),
                }
                for m in self.by_medication
            ],
        }


def _counts_json(counts: Counts) -> dict[str, Any]:
    return {
        "taken": counts.taken,
        "skipped": counts.skipped,
        "missed": counts.missed,
        "percentage": counts.percentage,
    }


def summarize(entries: list[HistoryEntry]) -> Summary:
    groups: dict[uuid.UUID, list[HistoryEntry]] = {}
    for entry in entries:
        groups.setdefault(entry.medication_id, []).append(entry)
    by_medication = sorted(
        (
            MedicationSummary(med_id, group[0].medication_name, count(group))
            for med_id, group in groups.items()
        ),
        key=lambda m: m.medication_name.lower(),
    )
    return Summary(counts=count(entries), by_medication=by_medication)


def _latin1(text: str) -> str:
    # Las fuentes base del PDF solo cubren latin-1: lo demás (emojis, otros alfabetos) pasa a "?".
    return text.encode("latin-1", "replace").decode("latin-1")


def _percentage(value: float | None) -> str:
    return "sin datos" if value is None else f"{value:.1f} %".replace(".", ",")


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
        pdf.set_font("Helvetica", "", 11)
        with pdf.table(
            col_widths=(46, 14, 14, 14, 12),
            text_align=("LEFT", "CENTER", "CENTER", "CENTER", "CENTER"),
        ) as table:
            header = table.row()
            for title in ("Medicamento", "Tomadas", "Omitidas", "Sin reg.", "%"):
                header.cell(title, style=None)
            for med in summary.by_medication:
                row = table.row()
                row.cell(_latin1(med.medication_name))
                row.cell(str(med.counts.taken))
                row.cell(str(med.counts.skipped))
                row.cell(str(med.counts.missed))
                row.cell(_percentage(med.counts.percentage))
    else:
        pdf.cell(0, 8, "No hubo dosis programadas en el periodo.", new_x="LMARGIN", new_y="NEXT")

    pdf.ln(8)
    pdf.set_font("Helvetica", "I", 9)
    pdf.multi_cell(
        0,
        5,
        f"Generado el {generated_at:%d/%m/%Y %H:%M} UTC. Informativo: no sustituye la opinion "
        "de un profesional de la salud. 'Sin registrar' son dosis que pasaron 60 minutos sin "
        "que el reloj registrara una toma.",
    )
    return bytes(pdf.output())
