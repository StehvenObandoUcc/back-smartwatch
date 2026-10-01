"""eventos de toma

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-01 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "dose_events",
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("device_id", sa.Uuid(), nullable=True),
        sa.Column("schedule_id", sa.Uuid(), nullable=False),
        sa.Column("medication_id", sa.Uuid(), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=8), nullable=False),
        sa.Column("acted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint("status IN ('TAKEN', 'SKIPPED')", name=op.f("ck_dose_events_status")),
        sa.ForeignKeyConstraint(
            ["device_id"],
            ["devices.id"],
            name=op.f("fk_dose_events_device_id_devices"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["medication_id"],
            ["medications.id"],
            name=op.f("fk_dose_events_medication_id_medications"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patients.id"],
            name=op.f("fk_dose_events_patient_id_patients"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dose_events")),
        sa.UniqueConstraint("event_id", name=op.f("uq_dose_events_event_id")),
        sa.UniqueConstraint("schedule_id", "scheduled_at", name=op.f("uq_dose_events_schedule_id")),
    )
    op.create_index(
        "ix_dose_events_patient_scheduled",
        "dose_events",
        ["patient_id", "scheduled_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_dose_events_patient_scheduled", table_name="dose_events")
    op.drop_table("dose_events")
