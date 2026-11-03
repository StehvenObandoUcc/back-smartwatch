# 0012 · Registro de accesos a datos de salud

Estado: aceptada · 2026-10-12

## Contexto

Para tratar datos de salud (Ley 1581 de 2012) hay que poder responder "quién vio o cambió los datos de este paciente y cuándo". Los accesos pasan por muchas rutas (web y reloj) y por muchos servicios; registrarlos uno a uno en cada servicio se olvidaría tarde o temprano.

## Decisiones

- **Tabla `health_data_access_log`** (migración 0012, solo inserciones): `actor_type` (`user` | `device`), `actor_id`, `patient_id`, `action` (`read` | `write`), `resource`, `method`, `status`, `request_id` y `created_at`. Índice `(patient_id, created_at DESC)` para consultar el rastro de un paciente. Nunca guarda contenido, solo el hecho del acceso.
- **Sin claves foráneas** en `actor_id` ni `patient_id`: el rastro debe sobrevivir al borrado de la cuenta del actor y al del paciente (ADR 0011). Es lo contrario de lo que se hace en el resto del esquema, y es a propósito.
- **Middleware en vez de servicios.** `AuditMiddleware` (ASGI puro, como `RequestContextMiddleware`) mira cada petición ya respondida; no hay que acordarse de llamar a nada en cada servicio y cubre por igual `/patients/{patientId}/...` (web) y `/devices/me/...` (reloj). `current_user` y `current_device` dejan el `Principal` autenticado en `request.state`, y la ruta casada (plantilla, `scope["route"]`) decide el recurso con una función pura (`classify_route`) que tiene pruebas unitarias.
- **Qué se audita:** `/patients/{patientId}` y todo lo que cuelga de él (`medications`, `schedules`, `plan`, `dose_events`, `dose_history`, `adherence`, `reports`, `chat`, `consents`, `caregivers`, `devices`; sin recurso conocido, `patient`), y del reloj `/devices/me/plan`, `/devices/me/dose-events` y `/devices/me/chat/messages` (el paciente sale de su token). Quedan fuera auth, `/users/me`, salud, el webhook de Telegram, la vinculación del reloj y `GET /patients` (no hay un paciente concreto). `read` para GET y HEAD, `write` para el resto.
- **Solo accesos con éxito (estado < 400).** Un 404 de un paciente no accesible no deja fila: no hubo acceso, y registrarlo permitiría llenar el rastro de un paciente con intentos ajenos.
- **Escritura con sesión propia y corta** (`session_factory`, commit inmediato), porque los GET nunca hacen commit. Si el insert falla se registra `audit_write_failed` con la clase de la excepción y no se propaga: la respuesta ya salió. El `request_id` sale del contexto de `structlog`, así que el middleware va dentro de `RequestContextMiddleware`.
- **SSE sin búfer:** el middleware solo observa `http.response.start`; la fila se escribe cuando termina el flujo.
- **Ajuste `AUDIT_ENABLED`** (por defecto `true`) para desactivarlo en local o en pruebas concretas.
- **Sin endpoint ni cambio de contrato** (sigue en 0.8.0): por ahora el rastro se consulta directamente en la base de datos, por ejemplo:
  `SELECT created_at, actor_type, actor_id, action, resource, method, status FROM health_data_access_log WHERE patient_id = '<uuid>' ORDER BY created_at DESC LIMIT 100;`

## Consecuencias

- **Coste:** un insert por petición auditada (una conexión y un commit más, tras enviar la respuesta). Es una escritura pequeña indexada; si el volumen lo exige, se pasará a un buffer en Redis con volcado por lotes.
- **Retención pendiente:** la tabla solo crece. Falta definir cuánto tiempo se guarda y cómo se purga (por ejemplo, particiones por mes); se decide en la fase de cierre.
- Un recurso nuevo bajo `/patients/{patientId}/...` queda registrado como `patient` hasta que se añada a `classify_route`; no se pierde el acceso.
- La API no ofrece todavía la consulta del rastro al paciente. Si hace falta (derecho de acceso), será un endpoint nuevo y aditivo.

## Cifrado en reposo a nivel de aplicación: no se implementa

Por decisión, la aplicación **no** cifra columnas de datos de salud. Se confía en discos cifrados o Postgres gestionado con cifrado en reposo, más TLS en tránsito, y eso se exigirá en el despliegue (fase de cierre). Motivos: el cifrado por columna impide consultar, filtrar e indexar por esos campos (el plan, la adherencia y los reportes se calculan con SQL), añade la gestión de claves y su rotación a la aplicación, y cuesta rendimiento en rutas con objetivo de p95 < 100 ms, sin cubrir el riesgo principal (acceso a la aplicación en ejecución, que ve los datos en claro de todos modos).
