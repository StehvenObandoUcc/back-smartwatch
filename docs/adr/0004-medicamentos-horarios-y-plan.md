# 0004 · Medicamentos, horarios simples y plan de 7 días

Estado: aceptada (sprint A del plan exprés) · 2026-10-01

## Decisiones

- **Horarios simples, sin RRULE.** Un horario es `times` (HH:MM) × `daysOfWeek` (ISO, 1 = lunes) entre `startDate` y `endDate` (fechas locales del paciente). Un medicamento admite hasta 10 horarios. Las horas se guardan como texto `HH:MM` en un array de Postgres.
- **El plan se calcula al pedirlo**, no se guarda: se generan las dosis de 7 días desde el inicio del día local del paciente (`patients.timezone`). Una dosis se identifica por `scheduleId` + `scheduledAt`, sin tabla de dosis.
- **Versión del plan:** `patients.plan_version` sube con `UPDATE ... SET plan_version = plan_version + 1` en la misma transacción que cada cambio de medicamento u horario. Es atómico y no pierde subidas concurrentes.
- **ETag** = `"<versión>-<fecha local>"`. Cambia con la versión o al cambiar el día local, porque la ventana de 7 días se desplaza. `If-None-Match` acepta lista, `W/` y `*`.
- **Archivar, no borrar** medicamentos (`archived_at`): deja de estar en el plan y conserva el historial. Los horarios sí se borran de verdad.
- **`Schedule.effective_from`** (se reinicia al editar el horario): el historial no marca como omitida una dosis anterior a esa fecha, porque ese horario no existía entonces. Lo mismo para dosis posteriores a `archived_at`.
- **Consentimiento `health_data`** del paciente: obligatorio para todo lo de este módulo (403 `consent_required`). Un paciente sin vínculo con el usuario es 404, como en el resto de la API.
- Sin caché en Redis: el `ETag` evita la descarga y la consulta usa los índices `ix_medications_patient_created` e `ix_schedules_medication_id`.

## Consecuencias

- Editar las horas de un horario cambia solo el futuro; las tomas ya registradas conservan su `scheduledAt`.
- Si más adelante se necesita RRULE, se añade como otro tipo de horario sin romper este contrato.
- Las dosis cercanas a un cambio de hora (DST) usan la hora local de pared: una hora inexistente se desplaza con las reglas de `zoneinfo`.
