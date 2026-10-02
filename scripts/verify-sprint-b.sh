#!/usr/bin/env bash
# Recorrido del sprint B contra la API local, sin claves reales de Resend, Telegram ni DeepSeek.
# Uso: bash scripts/verify-sprint-b.sh        (BASE_URL=http://localhost:8000 por defecto)
# Lee los enlaces de los correos de la tabla notifications_outbox (docker compose debe estar arriba).
# Con VERIFY_WORKER=1 comprueba además que el worker entrega el correo (arráncalo antes con
# OUTBOX_SCRUB_PAYLOAD=false y EMAIL_PROVIDER=console; ver README).
# Cada paso imprime OK o FALLÓ; al primer fallo se detiene con código 1.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

PASSWORD="Verifica-sprint-B-2026"
NEW_PASSWORD="Otra-clave-sprint-B-2027"
EMAIL="verifica.b.$(date +%s).$RANDOM@example.com"

# wait_sent KIND: espera hasta 45 s a que el último mensaje de ese tipo pase a 'sent'
wait_sent() {
  local status="" i
  for i in $(seq 1 45); do
    status="$(db_query "SELECT status FROM notifications_outbox WHERE kind = '$1' ORDER BY created_at DESC LIMIT 1")"
    [ "$status" = "sent" ] && return 0
    sleep 1
  done
  fail "El worker entrega el correo '$1' (estado final: ${status:-sin mensaje})" "¿está corriendo el worker? (uv run python -m arq app.worker.main.WorkerSettings)"
}

# outbox_token KIND [N]: deja en OUTBOX_TOKEN el token del enlace del correo más reciente (o el
# N-ésimo anterior) de ese tipo. Sin subshell: así un fallo detiene de verdad el script.
outbox_token() {
  local link
  link="$(db_query "SELECT payload->>'link' FROM notifications_outbox WHERE kind = '$1' AND payload->>'to' = '$EMAIL' ORDER BY created_at DESC OFFSET ${2:-0} LIMIT 1")"
  case "$link" in
    http*token=*) OUTBOX_TOKEN="${link##*token=}" ;;
    *) fail "Hay un correo '$1' en el outbox para $EMAIL" "respuesta de Postgres: '${link:-vacía}' (¿docker compose arriba y migraciones aplicadas?)" ;;
  esac
}

echo "Verificando el sprint B contra $BASE"
echo

request GET /health/ready
[ "$STATUS" = "200" ] || fail "La API responde y ve Postgres y Redis" "¿está arrancada la API y levantados Postgres y Redis?"
ok "La API responde y ve Postgres y Redis"

# ── Paso 1: verificación de correo y recuperación de contraseña ───────────────
request POST /auth/register "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\",\"displayName\":\"Luis\",\"role\":\"caregiver\",\"timezone\":\"America/Bogota\"}"
[ "$STATUS" = "201" ] || fail "Registrar un cuidador"
need_json "Registrar un cuidador" tokens.accessToken
TOKEN="$VALUE"
expect_eq "El usuario nace con el correo sin verificar" "false" "$(jget user.emailVerified)"

outbox_token verify_email
FIRST="$OUTBOX_TOKEN"
ok "El registro encoló el correo de verificación en el outbox"
if [ "${VERIFY_WORKER:-0}" = "1" ]; then
  wait_sent verify_email
  ok "El worker entregó el correo de verificación (estado sent)"
fi

request POST /auth/resend-verification "" "$TOKEN"
expect_eq "Reenviar la verificación responde 202" "202" "$STATUS"
outbox_token verify_email
SECOND="$OUTBOX_TOKEN"
[ "$FIRST" != "$SECOND" ] || fail "El reenvío genera un enlace nuevo" "el token no cambió"
ok "El reenvío genera un enlace nuevo"

request POST /auth/verify-email "{\"token\":\"$FIRST\"}"
expect_eq "El enlace anterior ya no sirve (400)" "400" "$STATUS"
expect_eq "El código de error es invalid_token" "invalid_token" "$(jget code)"

request POST /auth/verify-email "{\"token\":\"$SECOND\"}"
expect_eq "El enlace nuevo verifica el correo (204)" "204" "$STATUS"
request GET /users/me "" "$TOKEN"
expect_eq "GET /users/me muestra emailVerified true" "true" "$(jget emailVerified)"
request POST /auth/verify-email "{\"token\":\"$SECOND\"}"
expect_eq "El enlace es de un solo uso (400 al repetirlo)" "400" "$STATUS"

request POST /auth/resend-verification "" "$TOKEN"
expect_eq "Con el correo ya verificado el reenvío responde 204" "204" "$STATUS"

request POST /auth/forgot-password '{"email":"nadie.existe@example.com"}'
expect_eq "Recuperar con un correo inexistente responde 202 igual" "202" "$STATUS"
request POST /auth/forgot-password "{\"email\":\"$EMAIL\"}"
expect_eq "Recuperar con el correo real responde 202" "202" "$STATUS"
outbox_token reset_password
RESET="$OUTBOX_TOKEN"
ok "Se encoló el correo de recuperación en el outbox"

request POST /auth/reset-password "{\"token\":\"$RESET\",\"newPassword\":\"$NEW_PASSWORD\"}"
expect_eq "Fijar la contraseña nueva con el enlace (204)" "204" "$STATUS"
request POST /auth/reset-password "{\"token\":\"$RESET\",\"newPassword\":\"$NEW_PASSWORD\"}"
expect_eq "El enlace de recuperación es de un solo uso (400)" "400" "$STATUS"

request POST /auth/login "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}"
expect_eq "La contraseña vieja ya no entra (401)" "401" "$STATUS"
request POST /auth/login "{\"email\":\"$EMAIL\",\"password\":\"$NEW_PASSWORD\"}"
expect_eq "La contraseña nueva sí entra (200)" "200" "$STATUS"

# ── Paso 3: Telegram (el script hace de Telegram: llama al webhook con el secret_token) ───────
env_or_dotenv() { # NOMBRE: valor de la variable de entorno o, si no, de .env
  local value="${!1:-}"
  [ -n "$value" ] || value="$(grep -E "^$1=" .env 2>/dev/null | head -1 | cut -d= -f2- | tr -d '\r')"
  printf '%s' "$value"
}
WEBHOOK_SECRET="$(env_or_dotenv TELEGRAM_WEBHOOK_SECRET)"
BOT="$(env_or_dotenv TELEGRAM_BOT_USERNAME)"
[ -n "$WEBHOOK_SECRET" ] && [ -n "$BOT" ] || fail "Telegram configurado en .env" "faltan TELEGRAM_WEBHOOK_SECRET y TELEGRAM_BOT_USERNAME (cópialos de .env.example y reinicia la API)"
CHAT=$((RANDOM + 100000))
# webhook JSON [SECRETO]: lo que haría Telegram; sin segundo argumento envía el secreto correcto
webhook() { request POST /telegram/webhook "$1" "" "X-Telegram-Bot-Api-Secret-Token: ${2-$WEBHOOK_SECRET}"; }
tg_update() { # TEXTO: actualización de un chat privado con update_id único
  printf '{"update_id":%s%s,"message":{"message_id":1,"from":{"id":%s,"username":"verifica_tg"},"chat":{"id":%s,"type":"private"},"text":"%s"}}' "$(date +%s)" "$RANDOM" "$CHAT" "$CHAT" "$1"
}

request POST /auth/login "{\"email\":\"$EMAIL\",\"password\":\"$NEW_PASSWORD\"}"
need_json "Iniciar sesión para el paso de Telegram" tokens.accessToken
TOKEN="$VALUE"

request GET /users/me/notification-channels "" "$TOKEN"
expect_eq "Los canales empiezan con Telegram sin vincular" "false" "$(jget items.1.linked)"
expect_eq "El canal de correo aparece enmascarado" "v***@example.com" "$(jget items.0.label)"

request POST /users/me/notification-channels/telegram/link "" "$TOKEN"
expect_eq "Pedir el enlace de Telegram responde 201" "201" "$STATUS"
need_json "El enlace trae url" url
LINK="$VALUE"
case "$LINK" in "https://t.me/$BOT?start="*) ok "El enlace es https://t.me/$BOT?start=<token>" ;; *) fail "El enlace es https://t.me/$BOT?start=<token>" "recibido: $LINK" ;; esac
TG_TOKEN="${LINK##*start=}"

webhook "$(tg_update "/start $TG_TOKEN")" ""
expect_eq "El webhook sin secret_token responde 401" "401" "$STATUS"
webhook "$(tg_update "/start $TG_TOKEN")" "secreto-equivocado-0123456789"
expect_eq "El webhook con un secret_token distinto responde 401" "401" "$STATUS"
request GET /users/me/notification-channels "" "$TOKEN"
expect_eq "Con secreto incorrecto Telegram sigue sin vincularse" "false" "$(jget items.1.linked)"

webhook "$(tg_update "/start $TG_TOKEN")"
expect_eq "El webhook con el secret_token correcto responde 200" "200" "$STATUS"
request GET /users/me/notification-channels "" "$TOKEN"
expect_eq "Telegram queda vinculado" "true" "$(jget items.1.linked)"
expect_eq "La etiqueta es el usuario de Telegram" "@verifica_tg" "$(jget items.1.label)"
expect_eq "La confirmación del bot quedó en el outbox" "1" "$(db_query "SELECT count(*) FROM notifications_outbox WHERE channel = 'telegram' AND kind = 'telegram_linked' AND payload->>'chat_id' = '$CHAT'")"

webhook "$(tg_update "/start $TG_TOKEN")"
expect_eq "Reusar el token no rompe nada (200)" "200" "$STATUS"
expect_eq "El token de un solo uso genera la respuesta de enlace inválido" "1" "$(db_query "SELECT count(*) FROM notifications_outbox WHERE channel = 'telegram' AND kind = 'telegram_link_failed' AND payload->>'chat_id' = '$CHAT'")"

request GET /users/me/notification-preferences "" "$TOKEN"
expect_eq "Las preferencias empiezan con todo activado" "true" "$(jget weeklyReport.telegram)"
request PUT /users/me/notification-preferences '{"missedDose":{"email":true,"telegram":true},"weeklyReport":{"email":true,"telegram":false}}' "$TOKEN"
expect_eq "Guardar las preferencias responde 200" "200" "$STATUS"
request GET /users/me/notification-preferences "" "$TOKEN"
expect_eq "Las preferencias guardadas se leen igual (weeklyReport.email)" "true" "$(jget weeklyReport.email)"
expect_eq "Las preferencias guardadas se leen igual (weeklyReport.telegram)" "false" "$(jget weeklyReport.telegram)"

# ── Paso 4: alerta de dosis omitida y reporte semanal ───────────────────────────────────────────
skip() { STEP=$((STEP + 1)); printf 'OMITIDO %2d. %s\n' "$STEP" "$1"; }

request PUT /users/me/consents/notifications '{"granted":true,"version":"2026-10"}' "$TOKEN"
expect_eq "El cuidador otorga el consentimiento notifications" "200" "$STATUS"
request POST /patients '{"displayName":"Abuela","timezone":"America/Bogota"}' "$TOKEN"
need_json "Crear un paciente gestionado" id
PATIENT="$VALUE"
request PUT "/patients/$PATIENT/consents/health_data" '{"granted":true,"version":"2026-10"}' "$TOKEN"
expect_eq "Otorgar health_data del paciente" "200" "$STATUS"
request POST "/patients/$PATIENT/medications" '{"name":"Metformina","dosage":"1 tableta"}' "$TOKEN"
need_json "Crear un medicamento" id
MEDICATION="$VALUE"
# Una dosis que venció hace 90 minutos (hora de Bogotá, UTC-5): ya pasó la gracia de 60.
DOSE_TIME="$("$PY" -c 'from datetime import datetime, timedelta, timezone; print((datetime.now(timezone(timedelta(hours=-5))) - timedelta(minutes=90)).strftime("%H:%M"))')"
YESTERDAY="$("$PY" -c 'from datetime import datetime, timedelta, timezone; print((datetime.now(timezone(timedelta(hours=-5))) - timedelta(days=1)).date())')"
request POST "/patients/$PATIENT/medications/$MEDICATION/schedules" "{\"times\":[\"$DOSE_TIME\"],\"daysOfWeek\":[1,2,3,4,5,6,7],\"startDate\":\"$YESTERDAY\"}" "$TOKEN"
need_json "Crear un horario con una dosis vencida (a las $DOSE_TIME)" id
SCHEDULE="$VALUE"
# El horario "existía" desde antes (si no, las dosis anteriores a su creación no cuentan como omitidas).
db_query "UPDATE schedules SET effective_from = now() - interval '2 days' WHERE id = '$SCHEDULE'" >/dev/null
ok "El horario existía desde antes de la dosis (ajuste directo en la base)"

if [ "${VERIFY_WORKER:-0}" = "1" ]; then
  ALERTS=0
  for i in $(seq 1 90); do
    ALERTS="$(db_query "SELECT count(*) FROM notifications_outbox WHERE kind = 'missed_dose' AND (payload->>'to' = '$EMAIL' OR payload->>'chat_id' = '$CHAT')")"
    [ "$ALERTS" = "2" ] && break
    sleep 1
  done
  expect_eq "El worker encoló la alerta de dosis omitida (correo y Telegram)" "2" "$ALERTS"
  expect_eq "La alerta no incluye el medicamento" "0" "$(db_query "SELECT count(*) FROM notifications_outbox WHERE kind = 'missed_dose' AND payload::text LIKE '%Metformina%'")"
else
  skip "Alerta de dosis omitida (el worker la genera cada minuto: usa VERIFY_WORKER=1)"
fi

request POST "/patients/$PATIENT/reports" "" "$TOKEN"
expect_eq "Pedir un reporte responde 202" "202" "$STATUS"
expect_eq "El reporte nace pendiente" "pending" "$(jget status)"
REPORT="$(jget id)"
request POST "/patients/$PATIENT/reports" "" "$TOKEN"
expect_eq "Pedirlo otra vez devuelve el mismo reporte" "$REPORT" "$(jget id)"
request GET "/patients/$PATIENT/reports" "" "$TOKEN"
expect_eq "El listado incluye el reporte" "$REPORT" "$(jget items.0.id)"

if [ "${VERIFY_WORKER:-0}" = "1" ]; then
  REPORT_STATUS=""
  for i in $(seq 1 60); do
    request GET "/patients/$PATIENT/reports/$REPORT" "" "$TOKEN"
    REPORT_STATUS="$(jget status)"
    [ "$REPORT_STATUS" = "ready" ] && break
    sleep 1
  done
  expect_eq "El worker genera el reporte (ready)" "ready" "$REPORT_STATUS"
  expect_eq "El resumen cuenta la dosis sin registrar" "1" "$(jget summary.missed)"
  curl -sS -o "$WORK/reporte.pdf" -D "$HDRS" -H "Authorization: Bearer $TOKEN" "$BASE/patients/$PATIENT/reports/$REPORT/pdf"
  expect_eq "El PDF es un PDF" "%PDF" "$(head -c 4 "$WORK/reporte.pdf")"
  grep -qi '^content-type: application/pdf' "$HDRS" && ok "El PDF llega como application/pdf" || fail "El PDF llega como application/pdf"
else
  request GET "/patients/$PATIENT/reports/$REPORT/pdf" "" "$TOKEN"
  expect_eq "Sin generar, el PDF responde 409" "409" "$STATUS"
  skip "Generación del reporte y PDF (la hace el worker: usa VERIFY_WORKER=1)"
fi

# ── Paso 5: chat con IA (CHAT_PROVIDER=fake: responde sin red ni clave) ─────────────────────────
request POST "/patients/$PATIENT/chat/messages" '{"message":"Que me toca esta noche?"}' "$TOKEN"
expect_eq "Sin consentimiento ai_chat el chat responde 403" "403" "$STATUS"
expect_eq "El código es consent_required" "consent_required" "$(jget code)"
request PUT "/patients/$PATIENT/consents/ai_chat" '{"granted":true,"version":"2026-10"}' "$TOKEN"
expect_eq "Otorgar el consentimiento ai_chat del paciente" "200" "$STATUS"

request POST "/patients/$PATIENT/chat/messages" '{"message":"Que me toca esta noche?","history":[{"role":"user","content":"Hola"},{"role":"assistant","content":"Hola"}]}' "$TOKEN"
expect_eq "El chat web responde 200" "200" "$STATUS"
grep -qi '^content-type: text/event-stream' "$HDRS" && ok "La respuesta es text/event-stream (SSE)" || fail "La respuesta es text/event-stream (SSE)"
case "$BODY" in *"event: delta"*"event: done"*) ok "El flujo trae eventos delta y termina con done" ;; *) fail "El flujo trae eventos delta y termina con done" "$BODY" ;; esac
case "$BODY" in *'"remainingMessages": 29'*) ok "Quedan 29 mensajes (cupo diario de 30)" ;; *) fail "Quedan 29 mensajes (cupo diario de 30)" "$BODY" ;; esac
case "$BODY" in *'"text": "Respuesta "'*'"text": "noche?"'*) ok "El proveedor simulado recibió la pregunta (la repite en los trozos)" ;; *) fail "El proveedor simulado recibió la pregunta" "$BODY" ;; esac

request POST "/patients/$PATIENT/chat/messages" '{"message":""}' "$TOKEN"
expect_eq "Un mensaje vacío responde 422" "422" "$STATUS"

# El reloj: se vincula al paciente y pregunta por voz (respuesta corta en JSON).
request POST /devices/pairing-codes '{"model":"Script de verificacion"}'
need_json "El reloj pide un código de vinculación" code
WATCH_CODE="$VALUE"
need_json "El reloj pide un código de vinculación" deviceCode
WATCH_DEVICE_CODE="$VALUE"
request POST "/devices/pairing-codes/$WATCH_CODE/confirm" "{\"patientId\":\"$PATIENT\"}" "$TOKEN"
expect_eq "El cuidador vincula el reloj al paciente" "200" "$STATUS"
request POST /devices/token "{\"grantType\":\"device_code\",\"deviceCode\":\"$WATCH_DEVICE_CODE\"}"
need_json "El reloj recibe su token" accessToken
WATCH="$VALUE"

request POST /devices/me/chat/messages '{"message":"Que me toca esta noche?"}' "$WATCH"
expect_eq "El chat del reloj responde 200" "200" "$STATUS"
expect_eq "La respuesta corta llega en JSON" "Respuesta simulada a: Que me toca esta noche?" "$(jget reply)"
expect_eq "El cupo del reloj es aparte del de la web (quedan 29)" "29" "$(jget remainingMessages)"
request POST /devices/me/chat/messages '{"message":"Que me toca esta noche?"}' "$TOKEN"
expect_eq "Un token de usuario no sirve en el chat del reloj (403)" "403" "$STATUS"
request GET /users/me "" "$TOKEN"
USER_ID="$(jget id)"
request GET /devices/me "" "$WATCH"
DEVICE_ID="$(jget id)"
expect_eq "Se contó un mensaje de la web y uno del reloj (solo contadores, sin texto)" "1 1" "$(db_query "SELECT string_agg(messages::text, ' ') FROM chat_usage WHERE principal_id IN ('$USER_ID', '$DEVICE_ID')")"

webhook "$(tg_update "/stop")"
expect_eq "/stop responde 200" "200" "$STATUS"
request GET /users/me/notification-channels "" "$TOKEN"
expect_eq "/stop desvincula Telegram" "false" "$(jget items.1.linked)"
request DELETE /users/me/notification-channels/telegram "" "$TOKEN"
expect_eq "Desvincular de nuevo es idempotente (204)" "204" "$STATUS"

echo
echo "Todo OK: $STEP pasos verificados."
