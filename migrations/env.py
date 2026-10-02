import asyncio

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

# Cada módulo registra aquí sus modelos para que autogenerate los vea.
import app.modules.auth.models
import app.modules.consents.models
import app.modules.devices.models
import app.modules.doses.models
import app.modules.medications.models
import app.modules.notifications.models
import app.modules.patients.models
import app.modules.users.models  # noqa: F401
from app.core.config import get_settings
from app.core.db import Base

target_metadata = Base.metadata


def _database_url() -> str:
    # Orden: `alembic -x db_url=...`, atributo `db_url` (pruebas) y por último DATABASE_URL.
    url: str | None = context.get_x_argument(as_dictionary=True).get(
        "db_url"
    ) or context.config.attributes.get("db_url")
    return url or str(get_settings().database_url)


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(_database_url())
    async with engine.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
