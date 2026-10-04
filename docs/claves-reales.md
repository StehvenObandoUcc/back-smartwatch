# Claves reales: paso a paso

El backend funciona sin ninguna clave (correo y chat simulados, Telegram con el script de verificación). Estas son las que hacen falta para probar los canales reales. **Nunca las pegues en el chat, en un PR ni en un archivo que se suba a git**: van solo en tu archivo `.env` local (git lo ignora) o como variables de entorno.

Antes de empezar: `cp .env.example .env` (una vez) y abre `.env` con tu editor.

## 1. DeepSeek (chat con IA) — la más rápida

1. Entra en <https://platform.deepseek.com>, crea la cuenta y abre **API keys** → **Create new API key**. Copia la clave (solo se muestra una vez).
2. En **Top up** (o *Billing*) añade saldo: sin crédito la API responde 402.
3. En `.env`:
   ```
   CHAT_PROVIDER=deepseek
   DEEPSEEK_API_KEY=sk-...
   # DEEPSEEK_MODEL=deepseek-flash
   ```
4. Si la API responde "model not found", mira el nombre exacto en la lista de modelos de la plataforma y ponlo en `DEEPSEEK_MODEL` (el código por defecto es `deepseek-flash`).

Prueba: arranca la API, abre el chat en la web (o `POST /patients/{id}/chat/messages`) con el consentimiento `ai_chat` otorgado.

## 2. Telegram (avisos y vinculación)

1. En Telegram habla con **@BotFather** → `/newbot` → elige un nombre y un usuario que termine en `bot` (por ejemplo `MisRecordatoriosBot`). BotFather te da el **token** (`123456:ABC...`).
2. Genera el secreto del webhook (lo inventas tú; 16-256 caracteres `A-Za-z0-9_-`):
   ```sh
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```
3. En `.env`:
   ```
   TELEGRAM_BOT_TOKEN=123456:ABC...
   TELEGRAM_BOT_USERNAME=MisRecordatoriosBot      # sin @
   TELEGRAM_WEBHOOK_SECRET=<el secreto del paso 2>
   ```
4. Telegram necesita una **URL HTTPS pública** para llamar al webhook. En local, un túnel:
   ```sh
   cloudflared tunnel --url http://localhost:8000     # sin cuenta; imprime https://xxxx.trycloudflare.com
   ```
   (alternativa: `ngrok http 8000`). La URL cambia cada vez que reinicias el túnel.
5. Registra el webhook con esa URL (lee el token y el secreto de `.env`):
   ```sh
   bash scripts/telegram-webhook.sh set https://xxxx.trycloudflare.com
   bash scripts/telegram-webhook.sh info      # debe mostrar la URL y pending_update_count 0
   ```
6. **Arranca el worker** (envía los mensajes del bot): `PYTHONUTF8=1 uv run python -m arq app.worker.main.WorkerSettings`.

Prueba: en la web (o `POST /users/me/notification-channels/telegram/link`) pide el enlace de vinculación, ábrelo en Telegram y pulsa **Iniciar**; el bot debe contestar "Listo: este chat recibirá los avisos…". Escribe `/stop` para desvincular.

## 3. Resend (correo)

**Prueba rápida, sin dominio:** crea la cuenta en <https://resend.com>, **API Keys** → **Create API Key** (permiso *Sending access*). Sin dominio verificado, Resend solo deja enviar **desde `onboarding@resend.dev` al correo con el que te registraste**; sirve para ver llegar los correos de verificación y recuperación.
```
EMAIL_PROVIDER=resend
RESEND_API_KEY=re_...
EMAIL_FROM=Recordatorios <onboarding@resend.dev>
```
Regístrate en la app con ese mismo correo para recibir los mensajes.

**Producción (cualquier destinatario):** en Resend, **Domains** → **Add Domain**, y crea en el DNS de tu dominio los registros que indica (SPF y DKIM). Añade tú también el registro **DMARC** (TXT en `_dmarc.tudominio.com`, por ejemplo `v=DMARC1; p=none; rua=mailto:tu@correo`). Cuando Resend marque el dominio como *Verified*:
```
EMAIL_FROM=Recordatorios <avisos@tudominio.com>
```
Límite gratis: 100 correos al día y 3.000 al mes.

Prueba: registra una cuenta nueva (con el worker corriendo): en menos de 15 s llega el correo de verificación. El enlace apunta a `WEB_ORIGIN` (por defecto `http://localhost:5173`).

## Orden recomendado

1. DeepSeek (no necesita nada más).
2. Resend en modo prueba.
3. Telegram (necesita el túnel).

Con cada una lista: avisa y se prueba de punta a punta (el worker debe estar corriendo para correo y Telegram).

## Arranque completo (checklist E2E)

1. `docker compose up -d` y `uv run alembic upgrade head`.
2. API: `uv run python -m uvicorn app.main:create_app --factory --host 0.0.0.0`.
3. **Worker** (sin él no salen correos, alertas ni reportes): `PYTHONUTF8=1 uv run python -m arq app.worker.main.WorkerSettings`.
4. `GET /health` responde 200. Registra una cuenta y comprueba que llega el correo.
5. Al terminar: `docker compose stop`.

## Probar con el emulador del reloj o un teléfono

`uvicorn` escucha por defecto solo en `127.0.0.1`. Para que el emulador (`http://10.0.2.2:8000`) o un dispositivo real de la misma red alcancen la API:
```sh
uv run python -m uvicorn app.main:create_app --factory --host 0.0.0.0
```
Y deja pasar el puerto 8000 en el firewall de Windows si te lo pide.

## Si algo no llega

- Mensajes `pending` en la tabla `notifications_outbox` = el worker no está corriendo o el canal no tiene clave.
- Mensajes `failed` = error permanente del proveedor (`last_error` dice cuál, por ejemplo `resend: http 403` por dominio sin verificar o `telegram: http 403` porque el usuario bloqueó el bot).
- Webhook de Telegram: `bash scripts/telegram-webhook.sh info` muestra `last_error_message`.
