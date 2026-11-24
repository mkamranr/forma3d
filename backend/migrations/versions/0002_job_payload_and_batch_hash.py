"""job payload/diagnostics and operation batch hash/summary

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-24
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    with op.batch_alter_table("jobs") as batch:
        batch.add_column(sa.Column("payload", JSON, nullable=False, server_default=sa.text("'{}'")))
        batch.add_column(sa.Column("diagnostics", JSON, nullable=False, server_default=sa.text("'{}'")))
    with op.batch_alter_table("operation_batches") as batch:
        batch.add_column(sa.Column("batch_sha256", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("summary", sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    with op.batch_alter_table("operation_batches") as batch:
        batch.drop_column("summary")
        batch.drop_column("batch_sha256")
    with op.batch_alter_table("jobs") as batch:
        batch.drop_column("diagnostics")
        batch.drop_column("payload")
