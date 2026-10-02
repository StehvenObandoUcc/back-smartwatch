# back-smartwatch

API (FastAPI) de recordatorios de medicamentos para el reloj Wear OS y el panel web.
Contrato: [`contracts/openapi.yaml`](contracts/openapi.yaml). Plan: [`docs/plan-desarrollo-app.md`](docs/plan-desarrollo-app.md).

## Requisitos

- [uv](https://docs.astral.sh/uv/) (instala Python 3.13 con `uv python install 3.13`)
- Docker (Postgres y Redis de desarrollo y pruebas de integración)

## Arranque local

```sh
cp .env.example .env
docker compose up -d --wait        # Postgres 17 (puerto 5433) y Redis 7
uv sync
uv run python -m alembic upgrade head
uv run python -m uvicorn app.main:create_app --factory --reload
```

- `GET http://localhost:8000/health` y `GET http://localhost:8000/health/ready`
- Documentación interactiva (solo `APP_ENV=local`): http://localhost:8000/docs

## Verificar en local

Recorrido completo del sprint A contra la API real (Windows, Git Bash, desde la raíz del repo). Hace falta Docker Desktop abierto y `curl` y `python` en el PATH (vienen con Git Bash y Python). Una sola vez: `cp .env.example .env` y `uv sync`. Los comandos usan `uv run python -m ...` porque en esta máquina Windows el lanzador `uv run alembic` / `uv run uvicorn` falla con `uv trampoline failed to canonicalize script path`.

1. Levantar solo Postgres y Redis:

   ```sh
   docker compose up -d --wait
   ```

2. Aplicar las migraciones:

   ```sh
   uv run python -m alembic upgrade head
   ```

3. Arrancar la API (déjala corriendo en esa terminal; abre otra Git Bash para el paso 4):

   ```sh
   uv run python -m uvicorn app.main:create_app --factory
   ```

4. Correr el script (otra terminal):

   ```sh
   bash scripts/verify-sprint-a.sh
   bash scripts/verify-sprint-b.sh   # sprint B, sin claves reales (lee los correos del outbox en Postgres)
   ```

Imprime `OK` o `FALLÓ` por cada paso y termina con código distinto de cero si algo falla (`echo $?`). Cubre: registro de cuidador, paciente gestionado, consentimiento `health_data`, medicamento con horario, vinculación del reloj, plan con ETag y 304, lote de tomas con reenvío (`duplicate`), historial y adherencia. Cada ejecución crea un usuario nuevo; el registro admite 5 por hora desde la misma IP (si da 429: `docker compose exec redis redis-cli FLUSHDB`).

Al terminar: `docker compose stop`.

## Calidad (obligatorio antes de hacer push)

```sh
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest               # unitarias + integración (requiere Docker)
uv run pytest tests/unit    # sin Docker
```

## Estructura

```
app/
├── core/       config, logging, errores problem+json, db, middleware, dependencias
├── modules/    un paquete por dominio: router → service → repository
└── main.py     create_app()
migrations/     Alembic (async)
contracts/      openapi.yaml (fuente de verdad)
docs/adr/       decisiones de arquitectura
tests/          unit/ e integration/ (Testcontainers + Schemathesis)
```
