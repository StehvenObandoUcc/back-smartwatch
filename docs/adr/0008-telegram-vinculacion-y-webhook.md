# 0008 · Telegram: vinculación, webhook y preferencias

Estado: aceptada (sprint B, paso 3) · 2026-10-02

## Decisiones

- **Vinculación con token de un solo uso** (`https://t.me/<Bot>?start=<token>`): el bot solo puede escribir a quien lo inició. El token vive en `account_tokens` (propósito `link_telegram`, solo su SHA-256, caduca a los 15 min); pedir otro invalida el anterior. Un chat de Telegram pertenece a un solo usuario y un usuario tiene un solo chat: vincular reemplaza lo anterior.
- **El webhook valida `X-Telegram-Bot-Api-Secret-Token`** con `hmac.compare_digest` y **falla cerrado**: sin `TELEGRAM_WEBHOOK_SECRET` configurado, o con una cabecera ausente o distinta, responde 401 y no procesa nada. El secreto debe tener 16-256 caracteres `A-Za-z0-9_-` (lo que Telegram admite).
- **El webhook no envía nada en la petición**: las respuestas del bot (`telegram_linked`, `telegram_link_failed`, `telegram_unlinked`) se encolan en el outbox con `dedupe_key = telegram:<update_id>`, así que una reentrega de Telegram no contesta dos veces. Solo se procesan mensajes de texto de chats privados; grupos, ediciones y otros tipos se ignoran con 200.
- **`/stop` desvincula** el chat; `DELETE /users/me/notification-channels/telegram` lo hace desde el panel (idempotente).
- **Preferencias** en `notification_preferences` (cuatro booleanos por usuario); sin fila, todo activado. Reemplazo completo con PUT. Quién recibe cada aviso y la comprobación del consentimiento `notifications` se aplican al enviar (paso 4).
- **Secretos solo por entorno:** `TELEGRAM_BOT_TOKEN` y `TELEGRAM_WEBHOOK_SECRET`. El token va en la URL de la Bot API, y `httpx` registra las URLs a nivel INFO, por eso `configure_logging` sube `httpx` y `httpcore` a WARNING (hay una prueba). `scripts/telegram-webhook.sh` registra el webhook pasando la URL con el token por stdin a curl, para que no salga en la lista de procesos.
- **Sin token de bot el worker no registra el canal `telegram`**: los mensajes quedan pendientes en el outbox hasta que haya clave.
- Un `403` de Telegram (el usuario bloqueó el bot) o `400` (chat inexistente) es error permanente: el mensaje pasa a `failed`.

## Consecuencias

- Para probar de punta a punta hacen falta un bot real (`@BotFather`), una URL HTTPS pública y `scripts/telegram-webhook.sh set <url>`. Sin ellos, `verify-sprint-b.sh` hace de Telegram llamando al webhook con el secreto.
- Si el usuario bloquea el bot, el vínculo sigue en la base de datos hasta que use `/stop` o lo desvincule en el panel (los mensajes fallan sin reintentar).
