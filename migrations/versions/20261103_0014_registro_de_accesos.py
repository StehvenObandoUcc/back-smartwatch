"""registro de accesos a datos de salud

Tabla de solo inserciones con quién accedió a los datos de salud de qué paciente. `actor_id` y
`patient_id` no son claves foráneas a propósito: el rastro debe sobrevivir al borrado de la cuenta
del actor (migración 0011) y al del paciente.

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-03 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | Sequence[str] | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "health_data_access_log",
        sa.Column("actor_type", sa.String(length=8), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=8), nullable=False),
        sa.Column("resource", sa.String(length=32), nullable=False),
        sa.Column("method", sa.String(length=8), nullable=False),
        sa.Column("status", sa.SmallInteger(), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "action IN ('read', 'write')", name=op.f("ck_health_data_access_log_action")
        ),
        sa.CheckConstraint(
            "actor_type IN ('user', 'device')", name=op.f("ck_health_data_access_log_actor_type")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_health_data_access_log")),
    )
    op.create_index(
        "ix_health_data_access_log_patient_created",
        "health_data_access_log",
        ["patient_id", sa.literal_column("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_health_data_access_log_patient_created", table_name="health_data_access_log")
    op.drop_table("health_data_access_log")
