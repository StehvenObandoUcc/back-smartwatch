#!/usr/bin/env bash
# Recorrido completo del sprint A contra la API local, solo con curl.
# Uso: bash scripts/verify-sprint-a.sh        (BASE_URL=http://localhost:8000 por defecto)
# Cada paso imprime OK o FALLÓ; al primer fallo se detiene con código 1.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

PASSWORD="Verifica-sprint-A-2026"
EMAIL="verifica.$(date +%s).$RANDOM@example.com"
# Bogotá es UTC-5 todo el año: ayer en hora local sin depender de la base de datos de zonas.
YESTERDAY="$("$PY" -c 'from datetime import datetime, timedelta, timezone; print((datetime.now(timezone(timedelta(hours=-5))) - timedelta(days=1)).date())')"

echo "Verificando el sprint A contra $BASE"
echo

# 0. API arriba y con sus dependencias
request GET /health/ready
[ "$STATUS" = "200" ] || fail "La API responde y ve Postgres y Redis (GET /health/ready)" "¿está arrancada la API y levantados Postgres y Redis?"
ok "La API responde y ve Postgres y Redis"

# 1. Cuidador
request POST /auth/register "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\",\"displayName\":\"Luis\",\"role\":\"caregiver\",\"timezone\":\"America/Bogota\"}"
[ "$STATUS" = "201" ] || fail "Registrar un cuidador"
need_json "Registrar un cuidador" tokens.accessToken
TOKEN="$VALUE"
ok "Registrar un cuidador ($EMAIL)"

# 2. Paciente gestionado
request POST /patients '{"displayName":"Abuela","timezone":"America/Bogota"}' "$TOKEN"
[ "$STATUS" = "201" ] || fail "Crear un paciente gestionado"
need_json "Crear un paciente gestionado" id
PATIENT="$VALUE"
ok "Crear un paciente gestionado"

# 3. Consentimiento
request PUT "/patients/$PATIENT/consents/health_data" '{"granted":true,"version":"2026-10"}' "$TOKEN"
expect_eq "Otorgar el consentimiento health_data en nombre del paciente" "200" "$STATUS"

# 4. Medicamento y horario
request POST "/patients/$PATIENT/medications" '{"name":"Metformina","dosage":"1 tableta","instructions":"Con comida"}' "$TOKEN"
[ "$STATUS" = "201" ] || fail "Crear un medicamento"
need_json "Crear un medicamento" id
MEDICATION="$VALUE"
ok "Crear un medicamento"

request POST "/patients/$PATIENT/medications/$MEDICATION/schedules" \
  "{\"times\":[\"08:00\",\"20:00\"],\"daysOfWeek\":[1,2,3,4,5,6,7],\"startDate\":\"$YESTERDAY\"}" "$TOKEN"
[ "$STATUS" = "201" ] || fail "Crear un horario diario a las 08:00 y 20:00"
need_json "Crear un horario diario a las 08:00 y 20:00" id
SCHEDULE="$VALUE"
ok "Crear un horario diario a las 08:00 y 20:00"

# 5. Vincular el reloj (lo que haría el emulador): código, confirmación desde la web, token
request POST /devices/pairing-codes '{"model":"Script de verificacion"}'
[ "$STATUS" = "201" ] || fail "El reloj pide un código de vinculación"
need_json "El reloj pide un código de vinculación" code
CODE="$VALUE"
need_json "El reloj pide un código de vinculación" deviceCode
DEVICE_CODE="$VALUE"
ok "El reloj pide un código de vinculación ($CODE)"

request POST "/devices/pairing-codes/$CODE/confirm" "{\"patientId\":\"$PATIENT\"}" "$TOKEN"
expect_eq "El cuidador confirma el código desde la web" "200" "$STATUS"

request POST /devices/token "{\"grantType\":\"device_code\",\"deviceCode\":\"$DEVICE_CODE\"}"
[ "$STATUS" = "200" ] || fail "El reloj recibe sus tokens"
need_json "El reloj recibe sus tokens" accessToken
WATCH="$VALUE"
ok "El reloj recibe sus tokens"

# 6. Plan con ETag
request GET /devices/me/plan "" "$WATCH"
[ "$STATUS" = "200" ] || fail "El reloj descarga el plan"
ETAG="$(grep -i '^etag:' "$HDRS" | tr -d '\r' | sed 's/^[Ee][Tt][Aa][Gg]: *//')"
[ -n "$ETAG" ] || fail "El plan trae cabecera ETag" "no hay cabecera ETag en la respuesta"
ok "El reloj descarga el plan (ETag $ETAG)"
expect_eq "El plan tiene versión 2 (medicamento + horario)" "2" "$(jget version)"
DOSES="$(printf '%s' "$BODY" | "$PY" -c 'import json,sys; print(len(json.load(sys.stdin)["doses"]))')"
expect_eq "El plan trae 14 dosis (2 al día x 7 días)" "14" "$DOSES"
need_json "El plan trae la primera dosis" doses.0.scheduledAt
SCHEDULED_AT="$VALUE"
expect_eq "La primera dosis es del horario creado" "$SCHEDULE" "$(jget doses.0.scheduleId)"

request GET /devices/me/plan "" "$WATCH" "If-None-Match: $ETAG"
expect_eq "Con If-None-Match igual responde 304" "304" "$STATUS"
[ -z "$BODY" ] || fail "El 304 no trae cuerpo" "cuerpo recibido: $(printf '%s' "$BODY" | head -c 300)"
ok "El 304 no trae cuerpo"

# 7. Lote de eventos idempotente
EVENT_ID="$("$PY" -c 'import uuid; print(uuid.uuid4())')"
EVENTS="{\"events\":[{\"eventId\":\"$EVENT_ID\",\"scheduleId\":\"$SCHEDULE\",\"scheduledAt\":\"$SCHEDULED_AT\",\"status\":\"TAKEN\",\"actedAt\":\"$SCHEDULED_AT\"}]}"

request POST /devices/me/dose-events "$EVENTS" "$WATCH"
[ "$STATUS" = "200" ] || fail "El reloj envía un lote con una toma"
expect_eq "La toma se guarda (outcome created)" "created" "$(jget results.0.outcome)"

request POST /devices/me/dose-events "$EVENTS" "$WATCH"
[ "$STATUS" = "200" ] || fail "El reloj reenvía el mismo lote"
expect_eq "Reenviar el lote da duplicate (sin duplicar la toma)" "duplicate" "$(jget results.0.outcome)"

# 8. Historial y adherencia (lo que verá la web)
request GET "/patients/$PATIENT/dose-history" "" "$TOKEN"
[ "$STATUS" = "200" ] || fail "El cuidador consulta el historial"
expect_eq "El historial muestra la toma como TAKEN" "TAKEN" "$(jget items.0.status)"
HISTORY_COUNT="$(printf '%s' "$BODY" | "$PY" -c 'import json,sys; print(len(json.load(sys.stdin)["items"]))')"
expect_eq "El historial tiene una sola entrada (la toma no se duplicó)" "1" "$HISTORY_COUNT"

request GET "/patients/$PATIENT/adherence" "" "$TOKEN"
[ "$STATUS" = "200" ] || fail "El cuidador consulta la adherencia"
expect_eq "Adherencia: 1 tomada" "1" "$(jget taken)"
expect_eq "Adherencia: 100 %" "100.0" "$(jget percentage)"

echo
echo "Todo OK: $STEP pasos verificados."
