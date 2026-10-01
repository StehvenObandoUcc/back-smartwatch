# Plan exprés (v3) · 2026-10-01

Sustituye las fases 2 a 6 del plan v2. Las fases 0 y 1 ya están hechas. Objetivo: tener el producto funcionando de punta a punta en **2 sprints** en lugar de 5 fases.

## Qué cambia en la forma de trabajar

| Antes | Ahora |
|---|---|
| Un PR de contrato por fase, con aprobación | **Un solo contrato por sprint**, aprobado una vez |
| OK de Steve entre cada PR | Los agentes fusionan solos cuando la CI está en verde. Steve revisa al final del sprint |
| Pruebas completas en local | En local solo lint y pruebas unitarias rápidas. **Lo pesado lo hace GitHub Actions** |
| Cobertura del 85 %, Storybook completo, Schemathesis, OpenTelemetry, Tiles, k6 | **Pospuestos al final.** Solo se exigen pruebas de reglas críticas: autorización, generación del plan, idempotencia de eventos y alarmas |
| Emulador en cada fase | Emulador **solo al cierre de cada sprint** (o un reloj físico por ADB wifi) |

## Simplificaciones técnicas

- **Sin FCM en el MVP:** el reloj descarga el plan con WorkManager cada 30 minutos y al abrir la app (`ETag` evita descargas inútiles). Así no hace falta configurar Firebase. FCM llega después.
- **Horarios simples en vez de RRULE completo:** horas del día + días de la semana + fecha de inicio y fin. Cubre casi todos los tratamientos.
- **Sin worker arq en el sprint A.** El marcado de dosis `MISSED` se calcula al consultar (o con una tarea programada sencilla). El worker entra en el sprint B con las notificaciones.

## Sprint A · MVP funcional (fases 2 + 3)

**Resultado:** crear un medicamento en la web → el reloj lo descarga y suena → el paciente pulsa Tomada → la web lo muestra en el historial.

- **Backend:** medicamentos (CRUD), horarios simples, generación del plan de 7 días versionado, `GET /devices/me/plan` con `ETag`, `POST /devices/me/dose-events` (lote, idempotente), historial y porcentaje de adherencia por paciente.
- **Web:** formulario de medicamento y horarios, agenda de hoy, historial con porcentaje de adherencia.
- **Reloj:** descarga del plan con WorkManager, reprogramación de alarmas, pantallas Inicio y Hoy, subida de eventos pendientes con WorkManager.

## Sprint B · Notificaciones e IA (fases 4 + 5)

- **Backend:** worker arq y outbox, Telegram (vinculación con `/start <token>`), correo (Resend), reporte semanal, alerta de dosis omitida, verificación de correo y recuperación de contraseña, chat con DeepSeek.
- **Web:** conectar Telegram, preferencias de notificación, ver el reporte, chat.
- **Reloj:** chat por voz.

## Cierre · Endurecimiento y lanzamiento

Lo que se pospuso: FCM, Storybook completo, Schemathesis, OpenTelemetry, Tiles y Complications, cobertura del 85 %, k6, revisión de seguridad, despliegue y publicación.

## Dónde se ejecuta

**Recomendado:** conectar GitHub a este proyecto de Claude. Así el desarrollo y las pruebas corren en la nube, sin usar la RAM del equipo de Steve. En local solo queda el emulador al cierre de cada sprint.
