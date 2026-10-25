"""batería del reloj

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-25 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | Sequence[str] | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("devices", sa.Column("battery_level", sa.SmallInteger(), nullable=True))
    op.add_column(
        "devices", sa.Column("battery_reported_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("devices", "battery_reported_at")
    op.drop_column("devices", "battery_level")
