"""agent trace: step kind/node/seq and admin-only LLM prompt capture on agent_runs

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON(none_as_null=True).with_variant(postgresql.JSONB(none_as_null=True, astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    with op.batch_alter_table("agent_runs") as batch:
        batch.add_column(sa.Column("kind", sa.String(length=16), nullable=False, server_default="agent"))
        batch.add_column(sa.Column("node", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("seq", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("debug", JSON, nullable=True))
    # Existing rows were LLM calls unless they came from the deterministic agents.
    op.execute("UPDATE agent_runs SET kind = 'llm' WHERE model_name IS NOT NULL AND model_name <> 'deterministic'")


def downgrade() -> None:
    with op.batch_alter_table("agent_runs") as batch:
        batch.drop_column("debug")
        batch.drop_column("seq")
        batch.drop_column("node")
        batch.drop_column("kind")
