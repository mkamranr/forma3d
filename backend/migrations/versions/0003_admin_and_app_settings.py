"""admin flag on users, app_settings (in-app configuration)

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-25
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value", JSON, nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_app_settings")),
    )
    # Existing installs: the oldest active account becomes the first admin (more via ADMIN_EMAILS).
    op.execute(
        "UPDATE users SET is_admin = true WHERE id = "
        "(SELECT id FROM users WHERE is_active = true ORDER BY created_at LIMIT 1)"
    )


def downgrade() -> None:
    op.drop_table("app_settings")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("is_admin")
