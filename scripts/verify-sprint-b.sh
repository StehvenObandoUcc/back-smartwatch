#!/usr/bin/env bash
# Recorrido del sprint B contra la API local, sin claves reales de Resend, Telegram ni DeepSeek.
# Uso: bash scripts/verify-sprint-b.sh        (BASE_URL=http://localhost:8000 por defecto)
# Lee los enlaces de los correos de la tabla notifications_outbox (docker compose debe estar arriba).
# Cada paso imprime OK o FALLÓ; al primer fallo se detiene con código 1.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

PASSWORD="Verifica-sprint-B-2026"
NEW_PASSWORD="Otra-clave-sprint-B-2027"
EMAIL="verifica.b.$(date +%s).$RANDOM@example.com"

# outbox_token KIND [N]: token del enlace del N-ésimo (último por defecto) correo de ese tipo
outbox_token() {
  local link
  link="$(db_query "SELECT payload->>'link' FROM notifications_outbox WHERE kind = '$1' AND payload->>'to' = '$EMAIL' ORDER BY created_at DESC OFFSET ${2:-0} LIMIT 1")"
  [ -n "$link" ] || fail "Hay un correo '$1' en el outbox para $EMAIL" "no se encontró (¿docker compose arriba y migraciones aplicadas?)"
  printf '%s' "${link##*token=}"
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

FIRST="$(outbox_token verify_email)"
ok "El registro encoló el correo de verificación en el outbox"

request POST /auth/resend-verification "" "$TOKEN"
expect_eq "Reenviar la verificación responde 202" "202" "$STATUS"
SECOND="$(outbox_token verify_email)"
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
RESET="$(outbox_token reset_password)"
ok "Se encoló el correo de recuperación en el outbox"

request POST /auth/reset-password "{\"token\":\"$RESET\",\"newPassword\":\"$NEW_PASSWORD\"}"
expect_eq "Fijar la contraseña nueva con el enlace (204)" "204" "$STATUS"
request POST /auth/reset-password "{\"token\":\"$RESET\",\"newPassword\":\"$NEW_PASSWORD\"}"
expect_eq "El enlace de recuperación es de un solo uso (400)" "400" "$STATUS"

request POST /auth/login "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}"
expect_eq "La contraseña vieja ya no entra (401)" "401" "$STATUS"
request POST /auth/login "{\"email\":\"$EMAIL\",\"password\":\"$NEW_PASSWORD\"}"
expect_eq "La contraseña nueva sí entra (200)" "200" "$STATUS"

echo
echo "Todo OK: $STEP pasos verificados."
