# 0007 · Worker arq y entrega del outbox

Estado: aceptada (sprint B, paso 2) · 2026-10-02

## Decisiones

- **Un worker de arq** (`app/worker/main.py`) corre aparte de la API. Una tarea cron cada 15 s llama al `Dispatcher`. No se encola un trabajo por mensaje: el outbox en Postgres es la cola y es la fuente de verdad, así que un correo no se pierde aunque Redis se vacíe. Latencia máxima de entrega ≈ 15 s.
- **Un mensaje por transacción**, `SELECT ... FOR UPDATE SKIP LOCKED LIMIT 1`: varios workers no se pisan y el bloqueo de un mensaje no retiene a los demás. Entrega al menos una vez; Resend recibe `Idempotency-Key = dedupe_key`, así que un reenvío tras una caída no duplica el correo.
- **Reintentos con backoff exponencial** (60 s, 120 s, 240 s… tope 1 h, máximo 5 intentos). Errores de red, 429 y 5xx se reintentan; el resto de 4xx (clave mala, dirección inválida) pasan a `failed` sin reintentar. `last_error` y los logs guardan solo la causa (`resend: http 503`), nunca el cuerpo del proveedor, direcciones ni tokens.
- **El payload se borra al entregar o al dar por perdido** el mensaje (lleva enlaces con tokens): `OUTBOX_SCRUB_PAYLOAD=true` por defecto. En local se pone `false` para que los scripts de verificación lean los enlaces.
- **Proveedores intercambiables** detrás de `Sender` (`console` y `resend`). `console` escribe el correo completo en el log, por eso `Settings` lo rechaza en `staging` y `production`. `resend` exige `RESEND_API_KEY` (solo por entorno).
- **Windows:** arq registra señales con el loop de asyncio, que Windows no soporta; en esa plataforma se desactiva (`handle_signals`) y se para con Ctrl+C.

## Consecuencias

- Si el worker no corre, los mensajes quedan `pending` y salen cuando vuelva.
- El paso 3 (Telegram) añade un `Sender` para el canal `telegram`; hasta entonces el dispatcher ignora esos mensajes.
- `httpx` pasa de dependencia de desarrollo a dependencia de producción.
