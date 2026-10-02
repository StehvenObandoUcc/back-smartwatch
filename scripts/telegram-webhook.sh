#!/usr/bin/env bash
# Registra, consulta o borra el webhook del bot de Telegram. Necesita claves reales.
#   TELEGRAM_BOT_TOKEN=... TELEGRAM_WEBHOOK_SECRET=... bash scripts/telegram-webhook.sh set https://tu-dominio.com
#   TELEGRAM_BOT_TOKEN=... bash scripts/telegram-webhook.sh info
#   TELEGRAM_BOT_TOKEN=... bash scripts/telegram-webhook.sh delete
# Las claves salen del entorno (o de .env); nunca se escriben en el repositorio. La URL pública debe
# ser HTTPS (en local, un túnel como cloudflared o ngrok apuntando a http://localhost:8000).
set -eu
cd "$(dirname "${BASH_SOURCE[0]}")/.."

from_env() { # NOMBRE: variable de entorno o, si no, la línea de .env
  local value="${!1:-}"
  [ -n "$value" ] || value="$(grep -E "^$1=" .env 2>/dev/null | head -1 | cut -d= -f2- | tr -d '\r')"
  printf '%s' "$value"
}

TOKEN="$(from_env TELEGRAM_BOT_TOKEN)"
[ -n "$TOKEN" ] || { echo "Falta TELEGRAM_BOT_TOKEN (el que da @BotFather)" >&2; exit 1; }

# La URL (que lleva el token) viaja por stdin a curl: no aparece en la lista de procesos.
call() { # MÉTODO [líneas de configuración de curl...]
  local method="$1"
  shift
  { printf 'url = "https://api.telegram.org/bot%s/%s"\n' "$TOKEN" "$method"; printf '%s\n' "$@"; } \
    | curl -sS --fail-with-body -K -
  echo
}

case "${1:-}" in
  set)
    URL="${2:-}"
    case "$URL" in https://*) ;; *) echo "Uso: $0 set https://tu-dominio.com (HTTPS obligatorio)" >&2; exit 1 ;; esac
    SECRET="$(from_env TELEGRAM_WEBHOOK_SECRET)"
    [ -n "$SECRET" ] || { echo "Falta TELEGRAM_WEBHOOK_SECRET (16-256 caracteres A-Za-z0-9_-)" >&2; exit 1; }
    call setWebhook \
      "data-urlencode = \"url=${URL%/}/telegram/webhook\"" \
      "data-urlencode = \"secret_token=$SECRET\"" \
      'data-urlencode = "allowed_updates=[\"message\"]"' \
      'data-urlencode = "drop_pending_updates=true"'
    ;;
  info) call getWebhookInfo ;;
  delete) call deleteWebhook ;;
  *) echo "Uso: $0 {set <url-https>|info|delete}" >&2; exit 1 ;;
esac
