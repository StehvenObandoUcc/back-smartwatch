# Plan de desarrollo: reloj Wear OS + backend + panel web

Versión 2 · 2026-09-30 · Reemplaza la versión 1 (que tenía app de teléfono). Decisión de Steve: **no hay app de teléfono**. El producto es un reloj Wear OS independiente, un backend y un panel web sencillo para cargar medicamentos y ver reportes.

## 0. Decisiones

| Tema | Decisión |
|---|---|
| Producto | App **Wear OS independiente (standalone)** en Kotlin + backend FastAPI + **panel web** responsive |
| App de teléfono | **No.** El reloj habla directo con el backend por HTTPS (Wi-Fi, LTE o la conexión del teléfono vía Bluetooth) |
| Carga de datos | En el panel web (paciente o cuidador). El reloj solo muestra, suena y registra |
| Notificaciones | Telegram + correo (desde el backend). WhatsApp fuera |
| IA | Chat por voz desde el reloj → backend → DeepSeek (`deepseek-flash`) |
| Equipo | **Agente Backend** (repo `back-smartwatch`) y **Agente Frontend** (repo `front-smartwatch`: `wear/` y `web/`) |
| Contrato | `back-smartwatch/contracts/openapi.yaml`, dueño el Agente Backend, aprobado por Steve al inicio de cada fase |

## 1. Arquitectura

```
┌──────────────────────────┐        HTTPS (JWT)         ┌─────────────────────────┐
│ Reloj Wear OS (Kotlin)   │ ─────────────────────────► │ FastAPI (async)         │
│ Compose, Room, alarmas,  │ ◄── push FCM "plan nuevo" ─│ PostgreSQL · Redis      │
│ WorkManager, voz         │                            │ Worker (arq)            │
└──────────────────────────┘                            │  ├─► Telegram Bot API   │
┌──────────────────────────┐        HTTPS (JWT)         │  ├─► Correo (Resend)    │
│ Panel web (React + Vite) │ ─────────────────────────► │  ├─► FCM al reloj       │
│ medicamentos, horarios,  │                            │  └─► Reportes PDF       │
│ reportes, notificaciones │                            │ /chat ──► DeepSeek      │
└──────────────────────────┘                            └─────────────────────────┘
```

Principios:
- **El reloj funciona sin conexión.** Guarda el plan de los próximos 7 días en Room y programa alarmas locales. Internet solo hace falta para recibir cambios y enviar eventos.
- **El backend es la fuente de verdad** y genera el plan.
- **Nada lento en una petición HTTP**: correo, Telegram, push y PDF van al worker.
- **Contrato primero**: nada se implementa ni se consume sin estar antes en `openapi.yaml`.

## 2. Reloj Wear OS (Agente Frontend, `front-smartwatch/wear/`)

### 2.1 Stack
Kotlin, Jetpack Compose for Wear OS (Material 3), Horologist, Room, AlarmManager (`setAlarmClock`), NotificationManager, **WorkManager** (sincronización con red), DataStore + Android Keystore (tokens), Retrofit/OkHttp o Ktor + kotlinx.serialization con **cliente generado desde `openapi.yaml`** (openapi-generator), Firebase Cloud Messaging (aviso de plan nuevo), Tiles y Complications ("Próxima dosis"), Hilt, kotlinx.coroutines/Flow. Calidad: ktlint, detekt, JUnit, Turbine, Robolectric, Compose UI tests.

### 2.2 Qué cambia respecto al plan original del reloj
- **Sin Data Layer** (no hay app de teléfono). Se marca la app como independiente en el manifiesto (`com.google.android.wearable.standalone = true`).
- **WorkManager vuelve**: sube los eventos `PENDING` cuando hay red, con reintentos y backoff, y descarga el plan.
- **Vinculación del reloj sin teclado**: el reloj muestra un código corto (flujo tipo "device code", RFC 8628). El paciente o cuidador lo escribe en el panel web y el reloj queda asociado a ese paciente y recibe sus tokens.
- **Plan nuevo**: el backend envía un push FCM de datos; el reloj descarga `GET /devices/me/plan` (con `ETag`). Además, sincronización periódica cada pocas horas por si el push no llega.

### 2.3 Se mantiene del plan original
Room (`DoseEntity`, `DoseEventEntity`), alarmas locales con `setAlarmClock` y comprobación de `canScheduleExactAlarms()`, `BootReceiver`, notificación con acciones Tomada/Posponer (Omitir con confirmación), `eventId` UUID para no duplicar, pantallas Inicio / Hoy / Ajustes, `scheduledAt` en epoch UTC, posponer 10 min con máximo 3 veces.

### 2.4 Chat con la IA por voz
Botón de micrófono → reconocimiento de voz del sistema (`RecognizerIntent`) → `POST /chat/...` → respuesta corta en pantalla y leída con `TextToSpeech`. Respuestas limitadas a pocas frases porque es un reloj.

### 2.5 Estructura
```
wear/app/src/main/java/.../
├── ui/ (theme/, components/ atoms-molecules-organisms, screens/: Pairing, Home, Today, DoseAlert, Chat, Settings)
├── data/ (local/ Room, remote/ cliente generado, repository/)
├── sync/ (PlanSyncWorker, EventUploadWorker, FcmService)
├── alarm/ (DoseAlarmScheduler, DoseAlarmReceiver, DoseActionReceiver, BootReceiver, DoseNotifier)
├── tile/ y complication/
└── di/
```

## 3. Panel web (Agente Frontend, `front-smartwatch/web/`)

### 3.1 Stack
React + Vite + TypeScript `strict`, React Router, Tailwind CSS con tokens propios, TanStack Query + **cliente y hooks generados con Orval** (y mocks MSW), React Hook Form + Zod, i18next (español), Storybook, Vitest + Testing Library, Playwright (E2E), ESLint + Prettier.

### 3.2 Diseño atómico
```
web/src/
├── design/tokens        color, tipografía, espaciado, radios, sombras, breakpoints
├── components/atoms     Button, Input, Text, Badge, ColorDot, Icon, Spinner (sin dominio)
├── components/molecules FormField, DoseRow, MedicationChip, StatTile, ChannelToggle
├── components/organisms MedicationForm, ScheduleEditor, DoseTimeline, AdherenceChart, PairWatchDialog
├── components/templates AppShell (menú lateral / inferior), AuthLayout, SplitLayout
├── features/<dominio>   lógica y hooks
└── pages/               rutas: pantalla = template + organismos
```
Reglas: ningún valor visual literal fuera de `tokens/`; cada componente con historia en Storybook (normal, cargando, vacío, error, deshabilitado) y prueba.

### 3.3 Responsive y accesibilidad
Móvil < 640 px, tablet 640–1024, escritorio > 1024 (el cuidador lo abrirá a menudo desde el móvil). WCAG 2.2 AA, texto base 17 px o más, áreas táctiles de 44 px o más, foco visible, el color del medicamento siempre con su nombre.

### 3.4 Pantallas
Login/registro, vincular reloj (código), pacientes (si es cuidador), medicamentos y horarios, agenda de hoy y semana, adherencia e historial, reportes (ver/descargar PDF), notificaciones (Telegram, correo, preferencias), consentimientos.

## 4. Backend (Agente Backend, `back-smartwatch`)

### 4.1 Stack
Python 3.13, `uv`, FastAPI + Pydantic v2, Uvicorn con `uvloop`, SQLAlchemy 2 async + `asyncpg`, Alembic, Redis + `arq`, `ORJSONResponse`, JWT (acceso 15 min + refresh rotativo) y Argon2, `structlog`, OpenTelemetry, `ruff`, `mypy --strict`, `pytest` + `httpx` + Testcontainers, Schemathesis, k6.

**Docker:** `docker compose` levanta PostgreSQL y Redis para desarrollo y lo usan las pruebas de integración (Testcontainers). Confirmado por Steve: se usa Docker (docker compose) para PostgreSQL y Redis en desarrollo y Testcontainers en pruebas.

### 4.2 Módulos
`auth`, `users` (pacientes, cuidadores, consentimientos), `devices` (vinculación del reloj por código, token FCM), `medications`, `schedules` (RRULE y generación del plan de 7 días versionado), `doses` (eventos idempotentes, marcado de `MISSED`), `adherence`, `reports`, `notifications` (outbox, Telegram, correo, FCM), `chat` (DeepSeek por SSE para web y respuesta corta para el reloj).

Capas: `router → service → repository → models`. Todo asíncrono. Objetivo p95 < 100 ms lectura y < 150 ms escritura.

### 4.3 Endpoints clave nuevos por ser reloj independiente
- `POST /devices/pairing-codes` (el reloj pide un código) → `{ code, deviceCode, expiresIn, interval }`
- `POST /devices/pairing-codes/{code}/confirm` (el usuario lo confirma desde la web)
- `POST /devices/token` (el reloj consulta con `deviceCode` hasta recibir sus tokens)
- `PUT /devices/me/push-token`, `GET /devices/me/plan` (con `ETag`), `POST /devices/me/dose-events` (lote, idempotente)

### 4.4 Notificaciones
Outbox con `dedupe_key` y reintentos. Telegram: vinculación con `https://t.me/<Bot>?start=<token>` (el bot solo escribe a quien lo inició), webhook con `secret_token`, mensajes con resumen mínimo. Correo: Resend (3.000/mes, 100/día gratis), dominio con SPF, DKIM y DMARC. FCM para avisar al reloj de un plan nuevo.

## 5. Cómo trabajan los dos agentes

1. Cada agente escribe solo en su repo. El Agente Frontend trabaja en `wear/` y `web/` de `front-smartwatch`.
2. Al inicio de cada fase, el Agente Backend propone en un PR solo los cambios de `contracts/openapi.yaml`. **Steve lo aprueba** y los dos trabajan en paralelo.
3. El frontend genera sus clientes desde el contrato (Orval para web, openapi-generator para Kotlin) y usa mocks mientras la API no existe. Lee el contrato desde `../back-smartwatch/contracts/openapi.yaml` en local; en CI lo descarga del repo del backend fijando un commit.
4. El backend valida en CI que la API cumple el contrato (Schemathesis) y que no hay cambios incompatibles (oasdiff).
5. Una rama y un PR por tarea, commits convencionales, CI en verde. Decisiones importantes en `docs/adr/`.
6. Cada fase termina con una prueba real: backend levantado, reloj (emulador) y web contra la API real.

## 6. Fases

| # | Backend | Reloj | Web | Salida |
|---|---|---|---|---|
| 0 Fundaciones | Esqueleto FastAPI, `/health`, errores `problem+json`, Alembic, compose, CI | Proyecto Wear OS standalone. **Prototipo de alarma** con 2 dosis falsas en Room: suena, Tomada/Posponer, `BootReceiver`, permiso de alarmas exactas | Vite + tokens + átomos + Storybook + AppShell responsive, CI | La alarma suena en el emulador con el reloj en reposo; Storybook en 3 tamaños |
| 1 Cuentas y vinculación | Registro/login/refresh, roles, consentimientos, vinculación por código | Pantalla de vinculación con código, tokens en Keystore | Login/registro, consentimientos, "Vincular reloj" | El reloj queda vinculado a un paciente desde la web |
| 2 Medicamentos y plan | CRUD medicamentos, horarios RRULE, plan de 7 días versionado, push FCM al cambiar | Descarga del plan, Room, reprogramación de alarmas, pantallas Inicio y Hoy, Tile | Formulario de medicamento y horarios, agenda de hoy y semana | Crear un medicamento en la web y que suene en el reloj |
| 3 Tomas y adherencia | Eventos idempotentes en lote, marcado de `MISSED`, estadísticas | Cola offline con WorkManager, reintentos, estados en pantalla | Historial y gráfico de adherencia | En modo avión se marca una toma y se sincroniza sin duplicados |
| 4 Notificaciones y reportes | Outbox, Telegram, correo, reporte semanal PDF, alerta de dosis omitida | — | Conectar Telegram, correo, preferencias, reportes | El cuidador recibe el reporte por Telegram y correo |
| 5 IA | Chat con DeepSeek, contexto del plan sin datos identificativos, límites | Chat por voz con respuesta hablada | Chat en la web (opcional) | Preguntar por voz "¿qué me toca esta noche?" y oír la respuesta correcta |
| 6 Lanzamiento | Carga con k6, OWASP ASVS nivel 2, backups, despliegue | Batería, pruebas en reloj físico, Play Store (Wear) | Accesibilidad, E2E, despliegue | Checklist completo y política de privacidad publicada |

## 7. Riesgos

| Riesgo | Mitigación |
|---|---|
| Datos de salud (dato sensible, Ley 1581 de 2012) | Consentimiento por finalidad, cifrado, registro de accesos, borrado de cuenta |
| DeepSeek procesa datos en China | Consentimiento para el chat; no enviar nombre, documento ni contacto; proveedor intercambiable |
| El reloj sin conexión por mucho tiempo | Plan de 7 días en Room; la pantalla de inicio muestra "Plan válido hasta ..." |
| Alarmas que no suenan (permiso de alarmas exactas denegado por defecto en Android 14+) | Pantalla guiada de permiso; pruebas con Doze, reinicio y batería baja; se valida en la fase 0 |
| Wear OS 3+ solo se empareja con Android | Asumido: los pacientes usan teléfono Android. Apple Watch requeriría otra app en Swift |
| Batería del reloj | Sin sondeos frecuentes: push FCM + sincronización periódica espaciada |
| El push FCM no llega | Sincronización periódica de respaldo con WorkManager |
| Telegram sin cifrado de extremo a extremo en bots | Resumen mínimo y enlace al reporte dentro del panel con sesión iniciada |
| Correo en spam | Dominio propio con SPF, DKIM y DMARC |
| Límite gratuito de Resend | Alerta al 80 %; canal detrás de una interfaz para cambiar de proveedor |
| Coste y consejos peligrosos de la IA | Límites por usuario; guardarraíles; la IA no modifica el plan |
| Los dos agentes se pisan o el contrato se desalinea | Repos separados, contrato aprobado por Steve, Schemathesis y oasdiff en CI |
