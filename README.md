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
uv run alembic upgrade head
uv run uvicorn app.main:create_app --factory --reload
```

- `GET http://localhost:8000/health` y `GET http://localhost:8000/health/ready`
- Documentación interactiva (solo `APP_ENV=local`): http://localhost:8000/docs

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
