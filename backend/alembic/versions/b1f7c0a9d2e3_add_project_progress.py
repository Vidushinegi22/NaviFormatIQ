"""add project progress columns (current_step, completion)

These two columns let the wizard persist *where* the user is and *what* they
have finished, so reopening a project from the dashboard restores the full
pipeline instead of starting over. The model (app/models/project.py) declared
them, but the initial migration predated them and `create_all()` will not add
columns to an existing table — leaving the live Neon `projects` table without
them. Every `SELECT`/`UPDATE` of a project then referenced non-existent columns
and failed, which is why progress never persisted and reopen reset the wizard.

Revision ID: b1f7c0a9d2e3
Revises: 04e5540d176a
Create Date: 2026-05-29 19:20:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "b1f7c0a9d2e3"
down_revision: Union[str, None] = "04e5540d176a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _project_columns() -> set[str]:
    """Existing column names on `projects` (so this migration is idempotent —
    a fresh DB built via create_all already has the columns)."""
    insp = sa.inspect(op.get_bind())
    return {c["name"] for c in insp.get_columns("projects")}


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"
    cols = _project_columns()

    if "current_step" not in cols:
        op.add_column(
            "projects",
            sa.Column("current_step", sa.String(length=32), nullable=True),
        )

    if "completion" not in cols:
        json_type = sa.JSON().with_variant(
            postgresql.JSONB(astext_type=sa.Text()), "postgresql"
        )
        # NOT NULL to match the model; a server default backfills existing rows.
        server_default = sa.text("'{}'::jsonb") if is_pg else sa.text("'{}'")
        op.add_column(
            "projects",
            sa.Column(
                "completion",
                json_type,
                nullable=False,
                server_default=server_default,
            ),
        )


def downgrade() -> None:
    cols = _project_columns()
    if "completion" in cols:
        op.drop_column("projects", "completion")
    if "current_step" in cols:
        op.drop_column("projects", "current_step")
