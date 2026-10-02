"""correo de cuenta y outbox de notificaciones

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-02 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users", sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_table(
        "account_tokens",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("purpose", sa.String(length=16), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "purpose IN ('verify_email', 'reset_password')", name=op.f("ck_account_tokens_purpose")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_account_tokens_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_account_tokens")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_account_tokens_token_hash")),
    )
    op.create_index(
        "ix_account_tokens_user_purpose", "account_tokens", ["user_id", "purpose"], unique=False
    )
    op.create_table(
        "notifications_outbox",
        sa.Column("dedupe_key", sa.String(length=200), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=8), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "channel IN ('email', 'telegram')", name=op.f("ck_notifications_outbox_channel")
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'sent', 'failed')", name=op.f("ck_notifications_outbox_status")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notifications_outbox")),
        sa.UniqueConstraint("dedupe_key", name=op.f("uq_notifications_outbox_dedupe_key")),
    )
    op.create_index(
        "ix_notifications_outbox_due",
        "notifications_outbox",
        ["next_attempt_at"],
        unique=False,
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_notifications_outbox_due",
        table_name="notifications_outbox",
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.drop_table("notifications_outbox")
    op.drop_index("ix_account_tokens_user_purpose", table_name="account_tokens")
    op.drop_table("account_tokens")
    op.drop_column("users", "email_verified_at")
