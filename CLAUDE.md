# CLAUDE.md — Agente Backend

Eres el Agente Backend del proyecto de recordatorios de medicamentos. El producto es un **reloj Wear OS independiente** (sin app de teléfono) más un **panel web**; ambos los hace el Agente Frontend en el repo `front-smartwatch`. Tú trabajas en el repo `back-smartwatch`. El plan completo está en `docs/plan-desarrollo-app.md`.

## Tu territorio
- Escribes solo en este repo (`back-smartwatch`): `app/`, `contracts/`, `infra/`, `migrations/`, `tests/`, `docs/`.
- No editas `front-smartwatch`. Si el frontend debe cambiar algo, déjalo como comentario en el PR o issue.

## Contrato primero
- `contracts/openapi.yaml` es la fuente de verdad. Eres su dueño.
- Al empezar cada sprint, abre un PR que **solo** cambie el contrato con todas las operaciones del sprint. Con el plan exprés (`docs/plan-expres.md`) lo fusionas tú cuando la CI esté en verde y avisas a Steve en una línea; él revisa al final del sprint.
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

## Pruebas (plan exprés)
- En local solo `ruff check`, `ruff format --check`, `mypy --strict` y `pytest tests/unit`. Las de integración (Testcontainers) corren en CI.
- Pruebas exigidas en cada PR: autorización (una por regla), generación del plan, idempotencia de eventos y adherencia. El resto es opcional.
- Pospuestos hasta el cierre del proyecto: Schemathesis, OpenTelemetry y cobertura del 85 % en `modules/` (el umbral ya no está en `addopts`).
- Los agentes fusionan solos cuando la CI está en verde; si algo no se puede poner en verde, se detienen y avisan.
- Local en Windows: usa `uv run python -m mypy` y `uv run python -m pytest` (el lanzador `uv run mypy` falla).

## Flujo de trabajo
- Una rama y un PR por tarea (`feat/api-<tema>`), commits convencionales, PR pequeños.
- Descripción del PR: qué cambia, cómo se probó, qué endpoints del contrato cubre.
- Decisiones de diseño relevantes: una ADR en `docs/adr/NNNN-titulo.md`.
- Al cerrar cada fase: `docker compose up` completo y verificación con el reloj (emulador) y la web apuntando a la API real.
- Al cerrar cada fase, sube `info.x-closed-phase` del contrato a esa fase: la prueba `test_closed_phases_are_fully_implemented` exige entonces que todas las operaciones con `x-phase` menor o igual estén implementadas. Toda operación del contrato lleva `x-phase`.

## Notificaciones
- Canales: Telegram (bot con webhook y `secret_token`; vinculación con token de un solo uso vía `https://t.me/<Bot>?start=<token>`, porque el bot solo puede escribir a quien lo inició), correo (Resend, 3.000/mes y 100/día gratis; dominio con SPF, DKIM y DMARC) y FCM para avisar al reloj. WhatsApp NO se usa.
- Patrón outbox con `dedupe_key` y reintentos con backoff. Mensajes de Telegram con resumen mínimo y enlace al reporte dentro del panel (los chats con bots no tienen cifrado de extremo a extremo).

## Chat IA
- DeepSeek `deepseek-flash` con el SDK `openai` y `base_url=https://api.deepseek.com`, SSE para la web, respuesta corta para el reloj, guardarraíles (no cambia dosis, no diagnostica), límite de mensajes/tokens por usuario y día, registro de tokens.

## Docker
Confirmado por Steve. `docker compose` levanta solo PostgreSQL y Redis para desarrollo; la API corre nativa con `uv run`. Las pruebas de integración usan Testcontainers, que también necesita Docker.
- Postgres se publica en el puerto `5433` del host (configurable con `POSTGRES_PORT`), porque el `5432` lo ocupa un Postgres local. Redis en `6379` (`REDIS_PORT`).
- Contenedores con límite de memoria; detenlos con `docker compose stop` al terminar.

## Fases (tu parte)
0 Fundaciones: esqueleto FastAPI con core/, logging, errores problem+json, /health, Alembic, docker compose, CI.
1 Cuentas y vinculación: registro, login, refresh rotativo, roles paciente/cuidador, consentimientos, vinculación del reloj por código.
Plan exprés v3 (`docs/plan-expres.md`) sustituye las fases 2 a 6:
- Sprint A (fases 2+3): medicamentos, horarios simples (sin RRULE), plan de 7 días versionado con ETag, eventos de toma idempotentes, historial y adherencia. Sin FCM ni worker arq; MISSED se calcula al consultar.
- Sprint B (fases 4+5): worker arq y outbox, Telegram, correo, reporte semanal, alerta de dosis omitida, verificación de correo y recuperación de contraseña, chat con DeepSeek.
  Sprint B cerrado en el backend (`x-closed-phase: 3`): correo de cuenta, outbox y worker, Telegram, alertas y reportes, chat con DeepSeek. Pendiente de claves reales: Resend, bot de Telegram (`scripts/telegram-webhook.sh`) y DeepSeek.
- Cierre: FCM, Schemathesis, OpenTelemetry, cobertura 85 %, k6, ASVS, backups y despliegue.
