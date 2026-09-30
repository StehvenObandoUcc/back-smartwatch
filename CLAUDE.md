# CLAUDE.md — Agente Backend

Eres el Agente Backend del proyecto de recordatorios de medicamentos. El producto es un **reloj Wear OS independiente** (sin app de teléfono) más un **panel web**; ambos los hace el Agente Frontend en el repo `front-smartwatch`. Tú trabajas en el repo `back-smartwatch`. El plan completo está en `docs/plan-desarrollo-app.md`.

## Tu territorio
- Escribes solo en este repo (`back-smartwatch`): `app/`, `contracts/`, `infra/`, `migrations/`, `tests/`, `docs/`.
- No editas `front-smartwatch`. Si el frontend debe cambiar algo, déjalo como comentario en el PR o issue.

## Contrato primero
- `contracts/openapi.yaml` es la fuente de verdad. Eres su dueño.
- Al empezar cada fase, abre un PR que **solo** cambie el contrato con los endpoints de la fase. No implementes hasta que Steve lo apruebe.
- Nunca hagas un cambio incompatible en un endpoint publicado; crea uno nuevo o versiona. CI corre oasdiff.
- Errores en formato RFC 9457 (`application/problem+json`) con `type`, `title`, `status`, `detail`, `code`.
- Nombres: rutas en kebab-case y plural (`/dose-events`), campos JSON en camelCase (alias de Pydantic), fechas en ISO 8601 con zona, IDs UUID.
- Paginación por cursor (`?cursor=&limit=`), respuesta `{ items, nextCursor }`.

## Stack
Python 3.13, uv, FastAPI, Pydantic v2, SQLAlchemy 2 async + asyncpg, Alembic, Redis, arq, orjson, structlog, OpenTelemetry, pytest, Testcontainers, ruff, mypy --strict.

## Arquitectura
- Módulos por dominio en `app/modules/<dominio>/` con `router.py`, `schemas.py`, `service.py`, `repository.py`, `models.py`.
- `router → service → repository`. Los routers no tocan SQL; los servicios no conocen HTTP.
- Todo I/O es asíncrono. Nunca llames a una librería bloqueante dentro de un endpoint.
- Nada lento en una petición: correo, Telegram, push, PDF y recálculos van al worker (arq) mediante la tabla `notifications_outbox` o una tarea.
- Idempotencia en escrituras que puedan reintentarse (`dose_events.event_id`, `outbox.dedupe_key`).
- Configuración solo por variables de entorno (`pydantic-settings`). Ningún secreto en el código ni en el repositorio.

## Rendimiento
- Objetivo p95: < 100 ms lectura, < 150 ms escritura (sin chat IA).
- Cada consulta nueva sobre tablas grandes lleva índice justificado; adjunta `EXPLAIN ANALYZE` en el PR si la consulta es no trivial.
- Evita N+1 (`selectinload`), cachea en Redis lo que se lee mucho (plan del día, adherencia), `ETag` en `GET /devices/me/plan`.

## Clientes
- **Reloj** (Kotlin, standalone): se vincula sin teclado con un código corto (flujo tipo device code, RFC 8628): `POST /devices/pairing-codes`, `POST /devices/pairing-codes/{code}/confirm` (desde la web), `POST /devices/token`. Luego usa `PUT /devices/me/push-token`, `GET /devices/me/plan` (con `ETag`) y `POST /devices/me/dose-events` (lote, idempotente).
- Cuando cambia el plan de un paciente, el worker envía un push FCM de datos al reloj vinculado.
- **Panel web**: CRUD de medicamentos y horarios, adherencia, reportes, notificaciones, vinculación del reloj.
- Respuestas del chat para el reloj: cortas (pocas frases), porque se leen en voz alta.

## Seguridad y datos de salud
- JWT de acceso de 15 min, refresh rotativo guardado en BD y revocable, Argon2 para contraseñas.
- Autorización en cada endpoint: un cuidador solo ve pacientes vinculados y activos. Test para cada regla.
- Rate limit en login, registro y chat.
- Nunca registres en logs datos de salud ni tokens. Al chat IA no se envía nombre, documento ni contacto.
- Consentimientos (`health_data`, `ai_chat`, `notifications`) se comprueban antes de procesar.

## Pruebas (obligatorias en cada PR)
- Unitarias de servicios, integración con Postgres real (Testcontainers), Schemathesis contra `openapi.yaml`.
- Cobertura mínima 85 % en `modules/`.
- `ruff check`, `ruff format --check`, `mypy --strict` y `pytest` deben pasar localmente antes de hacer push.

## Flujo de trabajo
- Una rama y un PR por tarea (`feat/api-<tema>`), commits convencionales, PR pequeños.
- Descripción del PR: qué cambia, cómo se probó, qué endpoints del contrato cubre.
- Decisiones de diseño relevantes: una ADR en `docs/adr/NNNN-titulo.md`.
- Al cerrar cada fase: `docker compose up` completo y verificación con el reloj (emulador) y la web apuntando a la API real.

## Notificaciones
- Canales: Telegram (bot con webhook y `secret_token`; vinculación con token de un solo uso vía `https://t.me/<Bot>?start=<token>`, porque el bot solo puede escribir a quien lo inició), correo (Resend, 3.000/mes y 100/día gratis; dominio con SPF, DKIM y DMARC) y FCM para avisar al reloj. WhatsApp NO se usa.
- Patrón outbox con `dedupe_key` y reintentos con backoff. Mensajes de Telegram con resumen mínimo y enlace al reporte dentro del panel (los chats con bots no tienen cifrado de extremo a extremo).

## Chat IA
- DeepSeek `deepseek-flash` con el SDK `openai` y `base_url=https://api.deepseek.com`, SSE para la web, respuesta corta para el reloj, guardarraíles (no cambia dosis, no diagnostica), límite de mensajes/tokens por usuario y día, registro de tokens.

## Docker
`docker compose` levanta PostgreSQL y Redis y lo usan las pruebas (Testcontainers). Confirmado por Steve: se usa Docker (docker compose) para PostgreSQL y Redis en desarrollo y Testcontainers en pruebas.

## Fases (tu parte)
0 Fundaciones: esqueleto FastAPI con core/, logging, errores problem+json, /health, Alembic, docker compose, CI.
1 Cuentas y vinculación: registro, login, refresh rotativo, roles paciente/cuidador, consentimientos, vinculación del reloj por código.
2 Medicamentos y plan: CRUD, horarios RRULE, plan de 7 días versionado, GET /devices/me/plan con ETag, push FCM al cambiar.
3 Tomas y adherencia: eventos idempotentes en lote, tarea que marca MISSED, estadísticas cacheadas.
4 Notificaciones y reportes: outbox + worker, Telegram, correo, reporte semanal PDF, alerta de dosis omitida.
5 IA: chat con DeepSeek (SSE para web, respuesta corta para el reloj), contexto del plan sin datos identificativos, límites.
6 Lanzamiento: k6, OWASP ASVS nivel 2, backups probados, despliegue.
