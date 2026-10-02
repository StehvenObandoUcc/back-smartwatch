"""telegram y preferencias de notificación

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-02 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(op.f("ck_account_tokens_purpose"), "account_tokens", type_="check")
    op.create_check_constraint(
        op.f("ck_account_tokens_purpose"),
        "account_tokens",
        "purpose IN ('verify_email', 'reset_password', 'link_telegram')",
    )
    op.create_table(
        "telegram_links",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_telegram_links_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_telegram_links")),
        sa.UniqueConstraint("chat_id", name=op.f("uq_telegram_links_chat_id")),
        sa.UniqueConstraint("user_id", name=op.f("uq_telegram_links_user_id")),
    )
    op.create_table(
        "notification_preferences",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("missed_dose_email", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("missed_dose_telegram", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("weekly_report_email", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("weekly_report_telegram", sa.Boolean(), server_default="true", nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_notification_preferences_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_notification_preferences")),
    )


def downgrade() -> None:
    op.drop_table("notification_preferences")
    op.drop_table("telegram_links")
    op.drop_constraint(op.f("ck_account_tokens_purpose"), "account_tokens", type_="check")
    op.create_check_constraint(
        op.f("ck_account_tokens_purpose"),
        "account_tokens",
        "purpose IN ('verify_email', 'reset_password')",
    )
