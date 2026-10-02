# 0009 · Alerta de dosis omitida y reportes semanales

Estado: aceptada (sprint B, paso 4) · 2026-10-02

## Decisiones

- **Alerta de dosis omitida** (`notifications/alerts.py`, cron cada minuto): reutiliza `load_history`, así que "omitida" significa exactamente lo mismo que en el historial y la adherencia (60 min sin evento, solo dosis posteriores a `effective_from` y anteriores al archivado). Solo se alertan dosis de las últimas 6 h (`MISSED_ALERT_LOOKBACK_HOURS`): más viejas ya no sirven de aviso, y si el worker estuvo caído más tiempo esas alertas se pierden a propósito.
- **Una alerta por dosis, cuidador y canal**, con `dedupe_key = missed_dose:<horario>:<instante UTC>:<usuario>:<canal>`: repetir la pasada no duplica. La reciben **los cuidadores con vínculo activo**, no el paciente con cuenta.
- **Quién y por dónde** (`notifications/recipients.py`): un canal se usa solo si está vinculado (correo verificado / chat de Telegram), la preferencia lo permite y el destinatario concedió `notifications` (el de su registro de paciente si es un paciente con cuenta; el propio si es cuidador). Sin fila de preferencias, todo activado.
- **Resumen mínimo en los avisos:** ni el medicamento ni la dosis salen en la alerta; solo el nombre del paciente y el enlace al panel (en el correo, también la hora). El reporte semanal incluye el porcentaje y los totales, nunca nombres de medicamentos. Los chats de bots no van cifrados de extremo a extremo.
- **Reportes en el worker:** la API solo crea la fila `pending` (202) y el worker la genera cada 10 s (resumen + PDF con `fpdf2`, en un hilo aparte porque es CPU). El PDF se guarda en la fila (`bytea`, pocos KB): sin almacén de archivos. Un reporte fallido se reintenta pidiéndolo otra vez con el mismo `periodEnd`.
- **Un reporte por paciente y último día del periodo** (`UNIQUE (patient_id, period_end)`), de 7 días. Pedir el mismo periodo devuelve el existente.
- **Reporte semanal automático:** una tarea horaria crea el de la última semana cerrada (lunes a domingo) desde el lunes 08:00 en la zona del paciente y hasta el siguiente lunes (se pone al día si el worker estuvo caído). Solo para pacientes con algún horario en ese periodo. Al quedar listo avisa a cuidadores **y** al paciente con cuenta (idempotente por `report_id`/usuario/canal). Los reportes manuales no avisan.
- **Nombres en el PDF:** las fuentes base solo cubren latin-1; lo que no cabe (emojis, otros alfabetos) se sustituye por `?` en vez de romper la generación.

## Consecuencias

- Coste de la alerta: una pasada por minuto sobre todos los pacientes con horarios (carga el historial de ayer y hoy de cada uno). Si crece, guardar una marca de agua por paciente o filtrar por zona horaria.
- Los PDF viven en Postgres; si pesaran más o hubiera muchos, pasarían a un almacén de objetos.
- Sin `TELEGRAM_BOT_TOKEN` los avisos de Telegram quedan `pending` en el outbox hasta que haya clave.
