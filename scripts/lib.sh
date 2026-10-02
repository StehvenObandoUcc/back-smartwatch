# Utilidades comunes de los scripts de verificación (se cargan con `source`, no se ejecutan).
# BASE_URL=http://localhost:8000 por defecto.
# Los textos enviados son ASCII a propósito: curl en Windows puede mandar acentos en otra codificación.
# Cada paso imprime OK o FALLÓ; al primer fallo se detiene con código 1.
set -u
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1

BASE="${BASE_URL:-http://localhost:8000}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
HDRS="$WORK/headers"
BODYF="$WORK/body"
STATUS=""
BODY=""
STEP=0

# Intérprete de Python solo para leer JSON (no hace falta jq). Se prueba que ejecute de verdad.
PY=""
for candidate in python3 python; do
  if "$candidate" -c 'import json' >/dev/null 2>&1; then PY="$candidate"; break; fi
done
if [ -z "$PY" ]; then echo "FALLÓ  no hay Python en el PATH (se usa para leer JSON)"; exit 1; fi

ok() { STEP=$((STEP + 1)); printf 'OK      %2d. %s\n' "$STEP" "$1"; }

fail() {
  STEP=$((STEP + 1))
  printf 'FALLÓ   %2d. %s\n' "$STEP" "$1"
  [ -n "${2:-}" ] && printf '        %s\n' "$2"
  [ -n "$STATUS" ] && printf '        HTTP %s: %s\n' "$STATUS" "$(printf '%s' "$BODY" | head -c 600)"
  if [ "$STATUS" = "429" ]; then
    echo "        Límite de peticiones (el registro admite 5 por hora desde la misma IP)."
    echo "        Para reiniciarlo: docker compose exec redis redis-cli FLUSHDB"
  fi
  exit 1
}

# request METHOD RUTA [CUERPO_JSON] [TOKEN] [CABECERA_EXTRA] -> deja STATUS, BODY y $HDRS
request() {
  local method="$1" path="$2" body="${3:-}" token="${4:-}" extra="${5:-}"
  local args=(-sS -X "$method" -D "$HDRS" -o "$BODYF" -w '%{http_code}' --max-time 20)
  [ -n "$token" ] && args+=(-H "Authorization: Bearer $token")
  [ -n "$extra" ] && args+=(-H "$extra")
  [ -n "$body" ] && args+=(-H 'Content-Type: application/json' --data "$body")
  STATUS="" BODY=""
  : >"$BODYF" # con 304 (sin cuerpo) curl no escribe el archivo: no dejar el de la petición anterior
  if ! STATUS="$(curl "${args[@]}" "$BASE$path" 2>"$WORK/curl_err")"; then
    STATUS=""
    fail "$method $path" "curl: $(cat "$WORK/curl_err")"
  fi
  BODY="$(cat "$BODYF")"
}

# jget RUTA: valor de un campo del último cuerpo (a.b.0.c). Cadenas sin comillas; el resto en JSON.
jget() {
  printf '%s' "$BODY" | "$PY" -c '
import json, sys
try:
    d = json.load(sys.stdin)
    for key in sys.argv[1].split("."):
        d = d[int(key)] if isinstance(d, list) else d[key]
except Exception:
    sys.exit(1)
print(d if isinstance(d, str) else json.dumps(d))
' "$1"
}

# need DESCRIPCIÓN VALOR_ESPERADO VALOR_REAL
expect_eq() {
  if [ "$3" = "$2" ]; then ok "$1"; else fail "$1" "esperado '$2', recibido '$3'"; fi
}

# need_json DESCRIPCIÓN RUTA: el campo existe y no está vacío; imprime el valor en $VALUE
need_json() {
  VALUE="$(jget "$2")" || fail "$1" "falta el campo '$2' en la respuesta"
  [ -n "$VALUE" ] || fail "$1" "el campo '$2' está vacío"
}


# db_query SQL: ejecuta una consulta de solo lectura en el Postgres de docker compose (sin claves).
db_query() {
  docker compose exec -T postgres psql -U "${POSTGRES_USER:-smartwatch}"     -d "${POSTGRES_DB:-smartwatch}" -tA -c "$1" | tr -d ''
}
