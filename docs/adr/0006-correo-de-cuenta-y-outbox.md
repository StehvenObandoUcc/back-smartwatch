# 0006 · Verificación de correo, recuperación de contraseña y outbox

Estado: aceptada (sprint B, paso 1) · 2026-10-02

## Decisiones

- **Tokens de un solo uso** en `account_tokens` (propósito `verify_email` o `reset_password`), solo con su SHA-256. Caducan a las 24 h (verificación) y 1 h (recuperación). Pedir uno nuevo invalida los anteriores del mismo propósito. Se consumen con `SELECT ... FOR UPDATE`, así que el mismo enlace usado dos veces a la vez solo gana una.
- **El correo no se envía en la petición:** se encola en `notifications_outbox` en la misma transacción que lo causa (registro, reenvío, recuperación). `dedupe_key` único hace idempotente el encolado. El worker (paso 2) lo entrega con reintentos y backoff.
- **El outbox guarda `kind` + parámetros** (`{"to", "link"}`), no el texto; la plantilla (`notifications/templates.py`) lo arma al enviar. El enlace lleva el token en claro hasta que se envía: el worker debe borrar el `payload` del mensaje enviado.
- **`forgot-password` responde siempre 202** y solo envía si la cuenta existe **y** el correo está verificado (evita enviar enlaces a direcciones que nadie confirmó). Límite por IP y por correo.
- **`reset-password` revoca todas las sesiones web** del usuario (`refresh_tokens.user_id`). Los relojes vinculados no se tocan: se desvinculan desde la web.
- **El correo sin verificar no bloquea el uso de la cuenta**; solo condiciona la recuperación de contraseña y, en el paso 3, el canal de correo de las notificaciones.

## Consecuencias

- Hasta que exista el worker los correos quedan `pending` en el outbox (el script `verify-sprint-b.sh` los lee de ahí).
- `users.email_verified_at` es nulo en las cuentas anteriores a esta migración: tendrán que verificar su correo.
