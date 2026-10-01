"""medicamentos y horarios

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-01 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "patients", sa.Column("plan_version", sa.Integer(), server_default="0", nullable=False)
    )
    op.create_table(
        "medications",
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("dosage", sa.String(length=80), nullable=False),
        sa.Column("instructions", sa.String(length=500), nullable=True),
        sa.Column("color", sa.String(length=7), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patients.id"],
            name=op.f("fk_medications_patient_id_patients"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_medications")),
    )
    op.create_index(
        "ix_medications_patient_created",
        "medications",
        ["patient_id", "created_at", "id"],
        unique=False,
    )
    op.create_table(
        "schedules",
        sa.Column("medication_id", sa.Uuid(), nullable=False),
        sa.Column("times", postgresql.ARRAY(sa.String(length=5)), nullable=False),
        sa.Column("days_of_week", postgresql.ARRAY(sa.SmallInteger()), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column(
            "effective_from",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["medication_id"],
            ["medications.id"],
            name=op.f("fk_schedules_medication_id_medications"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_schedules")),
    )
    op.create_index(
        op.f("ix_schedules_medication_id"), "schedules", ["medication_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_schedules_medication_id"), table_name="schedules")
    op.drop_table("schedules")
    op.drop_index("ix_medications_patient_created", table_name="medications")
    op.drop_table("medications")
    op.drop_column("patients", "plan_version")
