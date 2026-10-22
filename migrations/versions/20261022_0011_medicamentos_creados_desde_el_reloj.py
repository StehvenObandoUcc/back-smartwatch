"""medicamentos creados desde el reloj (client_id idempotente)

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-22 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | Sequence[str] | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("medications", sa.Column("client_id", sa.Uuid(), nullable=True))
    # Los NULL (medicamentos creados en la web) no chocan entre sí.
    op.create_unique_constraint(
        op.f("uq_medications_patient_id"), "medications", ["patient_id", "client_id"]
    )


def downgrade() -> None:
    op.drop_constraint(op.f("uq_medications_patient_id"), "medications", type_="unique")
    op.drop_column("medications", "client_id")
