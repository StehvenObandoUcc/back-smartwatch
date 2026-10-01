# 0005 · Eventos de toma, historial y adherencia

Estado: aceptada (sprint A del plan exprés) · 2026-10-01

## Decisiones

- **Idempotencia:** `dose_events.event_id` es único y lo genera el reloj. El lote responde 200 con un resultado por evento: `created`, `duplicate` o `rejected` (con `code`). Un reintento se reconoce **antes** de validar, porque el horario pudo cambiar desde el primer envío.
- **Una dosis, un evento:** restricción única (`schedule_id`, `scheduled_at`). El segundo evento para la misma dosis se rechaza con `dose_already_recorded`. La inserción usa `ON CONFLICT DO NOTHING`, sin carreras entre lotes simultáneos.
- **Validación de la dosis:** el horario debe ser del paciente del reloj (`schedule_not_found`) y `scheduledAt` debe ser un instante que ese horario genera (`invalid_scheduled_at`). Los instantes se comparan en UTC, así que el reloj puede enviar cualquier desfase.
- **`schedule_id` sin clave foránea:** los horarios se borran de verdad y el evento conserva su historial.
- **`MISSED` se calcula al consultar** (sin worker): dosis sin evento con más de 60 minutos de retraso. No cuentan las dosis anteriores a `schedules.effective_from` (se reinicia al editar el horario) ni posteriores a `medications.archived_at`. Una dosis con evento aparece siempre, aunque esté dentro de la ventana de 60 minutos.
- **Adherencia** = tomadas / (tomadas + omitidas + MISSED) × 100, un decimal, `null` sin dosis vencidas. Rango por defecto: 7 días; máximo 90, en fechas locales del paciente.
- El historial se fusiona en memoria (eventos + dosis calculadas) y se pagina con el cursor `(scheduledAt, scheduleId)`. Techo: 90 días por consulta.

## Consecuencias

- Editar un horario hace que sus dosis pasadas sin evento dejen de contarse como omitidas: es el precio de no guardar las dosis.
- Cuando entre el worker (sprint B) se podrá persistir `MISSED` y calcular alertas sin cambiar el contrato.
